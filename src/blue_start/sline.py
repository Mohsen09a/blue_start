from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from .advanced import _build_hypergraph_arrays, _build_node_to_edge_csr
from .duckdb_backend import connect, export_query
from .pipeline import RunResult
from .settings import DuckDBSettings, load_settings


SLINE_WORK_NAME = "sline_full"


def _work_directory(settings: DuckDBSettings) -> Path:
    return settings.database.parent / SLINE_WORK_NAME


def _paths(work: Path) -> dict[str, Path]:
    return {
        "state": work / "state.json",
        "pack_ids": work / "pack_ids.uint32",
        "edge_offsets": work / "edge_offsets.int64",
        "edge_nodes": work / "edge_nodes.uint32",
        "node_offsets": work / "node_offsets.int64",
        "node_edges": work / "node_edges.uint32",
        "executable": work / "sline_full.exe",
        "batches": work / "batches",
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_remove(path: Path, settings: DuckDBSettings) -> None:
    expected = (settings.database.parent / SLINE_WORK_NAME).resolve()
    target = path.resolve()
    if target != expected:
        raise RuntimeError(f"Refusing to remove unexpected s-line path: {target}")
    if target.exists():
        shutil.rmtree(target)


def build_sline_input(*, rebuild: bool = False) -> RunResult:
    """Build compact bidirectional hypergraph CSR arrays for full s-line."""
    settings = load_settings()
    started = time.perf_counter()
    work = _work_directory(settings)
    paths = _paths(work)
    if rebuild:
        _safe_remove(work, settings)
    work.mkdir(parents=True, exist_ok=True)
    paths["batches"].mkdir(parents=True, exist_ok=True)

    if paths["state"].exists():
        state = json.loads(paths["state"].read_text(encoding="utf-8"))
        if bool(state.get("input_complete")):
            return RunResult(
                task="sline_full_input",
                seconds=time.perf_counter() - started,
                outputs=[str(path) for path in paths.values() if path.exists()],
                summary={**state, "reused_completed_input": True},
            )

    with connect(settings) as connection:
        pack_ids, node_ids, edge_offsets, edge_nodes = _build_hypergraph_arrays(
            connection
        )
    node_offsets, node_edges = _build_node_to_edge_csr(
        edge_offsets,
        edge_nodes,
        len(node_ids),
    )

    pack_ids.astype(np.uint32).tofile(paths["pack_ids"])
    edge_offsets.astype(np.int64).tofile(paths["edge_offsets"])
    edge_nodes.astype(np.uint32).tofile(paths["edge_nodes"])
    node_offsets.astype(np.int64).tofile(paths["node_offsets"])
    node_edges.astype(np.uint32).tofile(paths["node_edges"])
    state = {
        "input_complete": True,
        "deduplicated_set_semantics": True,
        "pack_count": int(len(pack_ids)),
        "node_count": int(len(node_ids)),
        "incidence_count": int(len(edge_nodes)),
        "maximum_pack_size": int(np.diff(edge_offsets).max(initial=0)),
        "build_seconds": time.perf_counter() - started,
    }
    _write_json(paths["state"], state)
    return RunResult(
        task="sline_full_input",
        seconds=time.perf_counter() - started,
        outputs=[
            str(paths["pack_ids"]),
            str(paths["edge_offsets"]),
            str(paths["edge_nodes"]),
            str(paths["node_offsets"]),
            str(paths["node_edges"]),
            str(paths["state"]),
        ],
        summary=state,
    )


def _build_native_runner(settings: DuckDBSettings) -> Path:
    work = _work_directory(settings)
    paths = _paths(work)
    source = Path(__file__).resolve().parents[2] / "native" / "sline_full.c"
    compiler = shutil.which("gcc")
    if compiler is None:
        bundled = Path("C:/mingw64/bin/gcc.exe")
        if bundled.exists():
            compiler = str(bundled)
    if compiler is None:
        raise RuntimeError("GCC is required to build native/sline_full.c")

    executable = paths["executable"]
    if (
        executable.exists()
        and executable.stat().st_mtime_ns >= source.stat().st_mtime_ns
    ):
        return executable
    command = [
        compiler,
        "-O3",
        "-march=native",
        "-fopenmp",
        "-std=c11",
        "-Wall",
        "-Wextra",
        str(source),
        "-o",
        str(executable),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Native s-line compilation failed:\n"
            + completed.stdout
            + completed.stderr
        )
    return executable


def _batch_paths(batch_root: Path, start: int, end: int) -> tuple[Path, Path, Path]:
    stem = f"{start:06d}_{end:06d}"
    return (
        batch_root / f"{stem}.hist.uint64",
        batch_root / f"{stem}.max.uint16",
        batch_root / f"{stem}.log",
    )


def _merge_batches(
    *,
    batch_root: Path,
    pack_count: int,
    s_max: int,
    batch_packs: int,
) -> tuple[np.ndarray, np.ndarray]:
    histogram = np.zeros(s_max + 1, dtype=np.uint64)
    maximum_overlap = np.zeros(pack_count, dtype=np.uint16)
    for start in range(0, pack_count, batch_packs):
        end = min(start + batch_packs, pack_count)
        hist_path, max_path, _ = _batch_paths(batch_root, start, end)
        batch_hist = np.fromfile(hist_path, dtype=np.uint64)
        batch_max = np.fromfile(max_path, dtype=np.uint16)
        if len(batch_hist) != s_max + 1 or len(batch_max) != pack_count:
            raise RuntimeError(f"Incomplete s-line batch output for {start}:{end}")
        histogram += batch_hist
        np.maximum(maximum_overlap, batch_max, out=maximum_overlap)
    return histogram, maximum_overlap


def compute_sline_full(
    *,
    s_max: int = 345,
    batch_packs: int = 4096,
    threads: int | None = None,
    maximum_new_batches: int | None = None,
    rebuild: bool = False,
) -> RunResult:
    """Compute unrestricted exact s-line counts with checkpointed native batches."""
    if s_max < 1 or s_max > 65_535:
        raise ValueError("s_max must be between 1 and 65,535")
    if batch_packs < 128:
        raise ValueError("batch_packs must be at least 128")
    if maximum_new_batches is not None and maximum_new_batches < 1:
        raise ValueError("maximum_new_batches must be positive")

    settings = load_settings()
    started = time.perf_counter()
    input_result = build_sline_input(rebuild=rebuild)
    work = _work_directory(settings)
    paths = _paths(work)
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    pack_count = int(state["pack_count"])
    node_count = int(state["node_count"])
    incidence_count = int(state["incidence_count"])
    executable = _build_native_runner(settings)
    thread_count = threads or min(settings.threads, os.cpu_count() or 1)

    batch_root = paths["batches"] / f"smax_{s_max}_batch_{batch_packs}"
    batch_root.mkdir(parents=True, exist_ok=True)
    new_batches = 0
    completed_batches = 0
    total_batches = (pack_count + batch_packs - 1) // batch_packs

    for start in range(0, pack_count, batch_packs):
        end = min(start + batch_packs, pack_count)
        hist_path, max_path, log_path = _batch_paths(batch_root, start, end)
        expected_hist_bytes = (s_max + 1) * np.dtype(np.uint64).itemsize
        expected_max_bytes = pack_count * np.dtype(np.uint16).itemsize
        if (
            hist_path.exists()
            and max_path.exists()
            and hist_path.stat().st_size == expected_hist_bytes
            and max_path.stat().st_size == expected_max_bytes
        ):
            completed_batches += 1
            continue
        if maximum_new_batches is not None and new_batches >= maximum_new_batches:
            break

        hist_tmp = hist_path.with_suffix(hist_path.suffix + ".tmp")
        max_tmp = max_path.with_suffix(max_path.suffix + ".tmp")
        hist_tmp.unlink(missing_ok=True)
        max_tmp.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment["OMP_NUM_THREADS"] = str(thread_count)
        print(
            f"[SLINE] batch {completed_batches + 1}/{total_batches}: "
            f"packs {start:,}:{end:,}",
            flush=True,
        )
        completed = subprocess.run(
            [
                str(executable),
                str(paths["edge_offsets"]),
                str(paths["edge_nodes"]),
                str(paths["node_offsets"]),
                str(paths["node_edges"]),
                str(pack_count),
                str(node_count),
                str(incidence_count),
                str(start),
                str(end),
                str(s_max),
                str(hist_tmp),
                str(max_tmp),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        log_path.write_text(
            completed.stdout + completed.stderr,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            hist_tmp.unlink(missing_ok=True)
            max_tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"Native s-line batch {start}:{end} failed; see {log_path}"
            )
        if (
            hist_tmp.stat().st_size != expected_hist_bytes
            or max_tmp.stat().st_size != expected_max_bytes
        ):
            raise RuntimeError(f"Native s-line batch {start}:{end} is incomplete")
        hist_tmp.replace(hist_path)
        max_tmp.replace(max_path)
        completed_batches += 1
        new_batches += 1
        print(completed.stdout.rstrip(), flush=True)

    complete = completed_batches == total_batches
    if not complete:
        summary = {
            **state,
            "complete": False,
            "s_max": s_max,
            "batch_packs": batch_packs,
            "threads": thread_count,
            "completed_batches": completed_batches,
            "total_batches": total_batches,
            "checkpoint_directory": str(batch_root),
        }
        return RunResult(
            task="sline_full_checkpoint",
            seconds=time.perf_counter() - started,
            outputs=[str(batch_root), str(paths["state"])],
            summary=summary,
        )

    histogram, maximum_overlap = _merge_batches(
        batch_root=batch_root,
        pack_count=pack_count,
        s_max=s_max,
        batch_packs=batch_packs,
    )
    edge_counts = np.cumsum(histogram[:0:-1], dtype=np.uint64)[::-1]
    node_counts = np.asarray(
        [
            np.count_nonzero(maximum_overlap >= s)
            for s in range(1, s_max + 1)
        ],
        dtype=np.uint32,
    )

    with connect(settings) as connection:
        connection.execute(
            """
            CREATE OR REPLACE TABLE results.s_line_full_local (
                s UINTEGER,
                nodes UINTEGER,
                edges UBIGINT
            )
            """
        )
        rows = [
            (s, int(node_counts[s - 1]), int(edge_counts[s - 1]))
            for s in range(1, s_max + 1)
        ]
        connection.executemany(
            "INSERT INTO results.s_line_full_local VALUES (?, ?, ?)",
            rows,
        )
        output = settings.parquet_outputs / "s_line_full_local.parquet"
        export_query(
            connection,
            "SELECT * FROM results.s_line_full_local ORDER BY s",
            output,
        )
        official_exists = connection.execute(
            """
            SELECT count(*) > 0
            FROM information_schema.tables
            WHERE table_schema = 'results'
              AND table_name = 'reference_s_line_counts'
            """
        ).fetchone()[0]
        differing_rows = None
        if official_exists:
            differing_rows = int(
                connection.execute(
                    """
                    SELECT count(*)
                    FROM (
                        SELECT * FROM results.s_line_full_local
                        EXCEPT
                        SELECT * FROM results.reference_s_line_counts
                        UNION ALL
                        SELECT * FROM results.reference_s_line_counts
                        EXCEPT
                        SELECT * FROM results.s_line_full_local
                    )
                    """
                ).fetchone()[0]
            )

    summary = {
        "complete": True,
        "exact": True,
        "unrestricted": True,
        "deduplicated_set_semantics": True,
        "s_min": 1,
        "s_max": s_max,
        "pack_count": pack_count,
        "node_count": node_count,
        "incidence_count": incidence_count,
        "distinct_s1_pack_pairs": int(edge_counts[0]),
        "s1_active_packs": int(node_counts[0]),
        "maximum_observed_overlap_at_least": int(
            np.flatnonzero(edge_counts > 0).max(initial=-1) + 1
        ),
        "batch_packs": batch_packs,
        "threads": thread_count,
        "total_batches": total_batches,
        "official_distribution_differing_rows": differing_rows,
        "official_distribution_exact_match": differing_rows == 0,
        "input_build_seconds": input_result.seconds,
    }
    result = RunResult(
        task="s_line_full_local",
        seconds=time.perf_counter() - started,
        outputs=[str(output), str(batch_root)],
        summary=summary,
    )
    summary_path = settings.summary_outputs / "s_line_full_local.json"
    _write_json(
        summary_path,
        {
            "task": result.task,
            "seconds": result.seconds,
            "outputs": result.outputs,
            "summary": summary,
        },
    )
    return RunResult(
        task=result.task,
        seconds=result.seconds,
        outputs=[*result.outputs, str(summary_path)],
        summary=result.summary,
    )
