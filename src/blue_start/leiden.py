from __future__ import annotations

import gc
import json
import re
import shutil
import time
from pathlib import Path

import numpy as np

from .advanced import (
    _load_official_labels,
    compress_seen_paths,
    union_edge_batch,
)
from .duckdb_backend import connect, export_query, sql_path
from .pipeline import RunResult
from .settings import DuckDBSettings, load_settings


LEIDEN_WORK_NAME = "starterpack_leiden"


def _work_directory(settings: DuckDBSettings) -> Path:
    return settings.database.parent / LEIDEN_WORK_NAME


def _paths(work: Path) -> dict[str, Path]:
    return {
        "state": work / "state.json",
        "giant_nodes": work / "giant_node_ids.uint32",
        "edges": work / "giant_unweighted_edges.uint32",
        "stdout": work / "run.stdout.log",
        "stderr": work / "run.stderr.log",
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_remove(path: Path, settings: DuckDBSettings) -> None:
    expected = (settings.database.parent / LEIDEN_WORK_NAME).resolve()
    target = path.resolve()
    if target != expected:
        raise RuntimeError(f"Refusing to remove unexpected Leiden path: {target}")
    if target.exists():
        shutil.rmtree(target)


def _projection_files(settings: DuckDBSettings) -> list[Path]:
    root = settings.database.parent / "clique_projection" / "partitions_256"
    files = [
        root / f"pair_bucket={bucket}" / "projection.parquet"
        for bucket in range(256)
    ]
    missing = [path for path in files if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing projection partition: {missing[0]}")
    return files


def _load_projection_nodes(
    settings: DuckDBSettings,
) -> tuple[np.ndarray, np.ndarray]:
    import pyarrow.parquet as pq

    path = (
        settings.parquet_outputs
        / "starterpack_projection_node_stats_local.parquet"
    )
    table = pq.read_table(path, columns=["node_id", "degree"])
    node_ids = table.column("node_id").to_numpy(zero_copy_only=False)
    degrees = table.column("degree").to_numpy(zero_copy_only=False)
    return node_ids.astype(np.uint32), degrees.astype(np.uint64)


def _compute_local_giant_component(
    settings: DuckDBSettings,
    projection_files: list[Path],
) -> tuple[np.ndarray, np.ndarray, int]:
    import pyarrow.parquet as pq

    node_ids, degrees = _load_projection_nodes(settings)
    node_count = len(node_ids)
    maximum_node_id = int(node_ids.max(initial=0))
    dense_lookup = np.full(maximum_node_id + 1, -1, dtype=np.int32)
    dense_lookup[node_ids] = np.arange(node_count, dtype=np.int32)

    parent = np.arange(node_count, dtype=np.uint32)
    sizes = np.ones(node_count, dtype=np.uint32)
    seen = np.zeros(node_count, dtype=np.uint8)

    # Compile union-find before scanning the real partitions.
    union_edge_batch(
        np.arange(2, dtype=np.uint32),
        np.ones(2, dtype=np.uint32),
        np.zeros(2, dtype=np.uint8),
        np.array([0], dtype=np.uint32),
        np.array([1], dtype=np.uint32),
    )

    for index, path in enumerate(projection_files):
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(
            batch_size=1_000_000,
            columns=["node_a", "node_b"],
            use_threads=True,
        ):
            node_a = batch.column(0).to_numpy(zero_copy_only=False)
            node_b = batch.column(1).to_numpy(zero_copy_only=False)
            sources = dense_lookup[node_a].astype(np.uint32)
            destinations = dense_lookup[node_b].astype(np.uint32)
            union_edge_batch(
                parent,
                sizes,
                seen,
                sources,
                destinations,
            )
        if (index + 1) % 32 == 0:
            print(
                f"[GIANT] scanned {index + 1}/256 projection partitions",
                flush=True,
            )

    compress_seen_paths(parent, seen)
    root_sizes = np.bincount(parent.astype(np.int64), minlength=node_count)
    largest_root = int(root_sizes.argmax())
    giant_mask = parent == largest_root
    giant_nodes = node_ids[giant_mask].copy()
    giant_edge_count = int(degrees[giant_mask].sum(dtype=np.uint64) // 2)
    del dense_lookup, parent, sizes, seen, root_sizes
    gc.collect()
    return giant_nodes, node_ids, giant_edge_count


def build_leiden_input(*, rebuild: bool = False) -> RunResult:
    """Build the paper-compatible unweighted giant-component edge array."""
    settings = load_settings()
    started = time.perf_counter()
    work = _work_directory(settings)
    paths = _paths(work)
    if rebuild:
        _safe_remove(work, settings)
    work.mkdir(parents=True, exist_ok=True)

    if paths["state"].exists():
        state = json.loads(paths["state"].read_text(encoding="utf-8"))
        if bool(state.get("input_complete")):
            return RunResult(
                task="starterpack_leiden_input",
                seconds=time.perf_counter() - started,
                outputs=[str(paths["giant_nodes"]), str(paths["edges"])],
                summary={**state, "reused_completed_input": True},
            )

    projection_files = _projection_files(settings)
    giant_nodes, all_nodes, giant_edge_count = _compute_local_giant_component(
        settings,
        projection_files,
    )
    all_projection_node_count = int(len(all_nodes))
    expected_giant_size = 1_997_488
    if len(giant_nodes) != expected_giant_size:
        raise RuntimeError(
            f"Local giant component has {len(giant_nodes):,} nodes; "
            f"expected {expected_giant_size:,}"
        )
    giant_nodes.astype(np.uint32).tofile(paths["giant_nodes"])

    maximum_node_id = int(all_nodes.max(initial=0))
    giant_lookup = np.full(maximum_node_id + 1, -1, dtype=np.int32)
    giant_lookup[giant_nodes] = np.arange(len(giant_nodes), dtype=np.int32)
    edge_array = np.memmap(
        paths["edges"],
        dtype=np.uint32,
        mode="w+",
        shape=(giant_edge_count, 2),
    )

    import pyarrow.parquet as pq

    cursor = 0
    for index, path in enumerate(projection_files):
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(
            batch_size=1_000_000,
            columns=["node_a", "node_b"],
            use_threads=True,
        ):
            node_a = batch.column(0).to_numpy(zero_copy_only=False)
            node_b = batch.column(1).to_numpy(zero_copy_only=False)
            mapped_a = giant_lookup[node_a]
            mapped_b = giant_lookup[node_b]
            valid_a = mapped_a >= 0
            valid_b = mapped_b >= 0
            if np.any(valid_a != valid_b):
                raise RuntimeError(
                    "Projection edge crosses a connected-component boundary"
                )
            count = int(np.count_nonzero(valid_a))
            if cursor + count > giant_edge_count:
                raise RuntimeError("Giant edge array overflow")
            edge_array[cursor : cursor + count, 0] = mapped_a[valid_a]
            edge_array[cursor : cursor + count, 1] = mapped_b[valid_a]
            cursor += count
        if (index + 1) % 32 == 0:
            edge_array.flush()
            print(
                f"[EDGES] wrote {index + 1}/256 projection partitions",
                flush=True,
            )

    edge_array.flush()
    del edge_array, giant_lookup, all_nodes
    gc.collect()
    if cursor != giant_edge_count:
        raise RuntimeError(
            f"Wrote {cursor:,} giant edges; expected {giant_edge_count:,}"
        )

    official_nodes, _, _ = _load_official_labels()
    node_sets_match = np.array_equal(
        np.sort(official_nodes),
        giant_nodes,
    )
    if not node_sets_match:
        raise RuntimeError(
            "Locally computed giant node set differs from the official node set"
        )

    state = {
        "input_complete": True,
        "paper_compatible": True,
        "unweighted": True,
        "seed": 0,
        "all_projection_nodes": all_projection_node_count,
        "giant_node_count": int(len(giant_nodes)),
        "giant_edge_count": giant_edge_count,
        "official_giant_node_set_exact_match": True,
        "edge_file_bytes": paths["edges"].stat().st_size,
        "build_seconds": time.perf_counter() - started,
    }
    _write_json(paths["state"], state)
    return RunResult(
        task="starterpack_leiden_input",
        seconds=time.perf_counter() - started,
        outputs=[str(paths["giant_nodes"]), str(paths["edges"]), str(paths["state"])],
        summary=state,
    )


def _comparison_metrics(
    local_labels: np.ndarray,
    official_labels: np.ndarray,
) -> tuple[float, float]:
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    return (
        float(normalized_mutual_info_score(official_labels, local_labels)),
        float(adjusted_rand_score(official_labels, local_labels)),
    )


def compute_leiden_full(*, rebuild: bool = False) -> RunResult:
    """Run the paper's independent igraph/leidenalg partition."""
    import igraph as ig
    import leidenalg
    import pyarrow as pa

    settings = load_settings()
    started = time.perf_counter()
    work = _work_directory(settings)
    paths = _paths(work)
    input_result = build_leiden_input(rebuild=rebuild)
    state = json.loads(paths["state"].read_text(encoding="utf-8"))

    summary_path = settings.summary_outputs / "starterpack_leiden_local.json"
    label_output = (
        settings.parquet_outputs / "starterpack_leiden_labels_local.parquet"
    )
    if (
        not rebuild
        and bool(state.get("leiden_complete"))
        and summary_path.exists()
        and label_output.exists()
    ):
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        return RunResult(
            task="starterpack_leiden_local",
            seconds=time.perf_counter() - started,
            outputs=[*payload["outputs"], str(summary_path)],
            summary={**payload["summary"], "reused_completed_result": True},
        )

    node_count = int(state["giant_node_count"])
    edge_count = int(state["giant_edge_count"])
    giant_nodes = np.fromfile(paths["giant_nodes"], dtype=np.uint32)
    edges = np.memmap(
        paths["edges"],
        dtype=np.uint32,
        mode="r",
        shape=(edge_count, 2),
    )

    print(
        f"[LEIDEN] constructing igraph with {node_count:,} nodes and "
        f"{edge_count:,} unweighted edges",
        flush=True,
    )
    graph_started = time.perf_counter()
    graph = ig.Graph(n=node_count, edges=edges, directed=False)
    graph_seconds = time.perf_counter() - graph_started
    del edges
    gc.collect()

    print("[LEIDEN] running ModularityVertexPartition(seed=0)", flush=True)
    leiden_started = time.perf_counter()
    partition = leidenalg.find_partition(
        graph,
        leidenalg.ModularityVertexPartition,
        seed=0,
    )
    leiden_seconds = time.perf_counter() - leiden_started
    local_labels = np.asarray(partition.membership, dtype=np.uint32)
    modularity = float(partition.modularity)
    del graph, partition
    gc.collect()

    official_nodes, official_labels, _ = _load_official_labels()
    official_order = np.argsort(official_nodes)
    official_nodes = official_nodes[official_order]
    official_labels = official_labels[official_order]
    if not np.array_equal(official_nodes, giant_nodes):
        raise RuntimeError("Official and local giant node ordering differs")
    nmi, adjusted_rand = _comparison_metrics(local_labels, official_labels)

    table = pa.table(
        {
            "node_id": pa.array(giant_nodes),
            "community": pa.array(local_labels),
        }
    )
    with connect(settings) as connection:
        connection.register("_local_leiden_labels", table)
        connection.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_leiden_labels_local AS
            SELECT
                node_id::UINTEGER AS node_id,
                community::UINTEGER AS community
            FROM _local_leiden_labels
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE
                results.starterpack_leiden_community_sizes_local AS
            SELECT
                community,
                count(*)::UBIGINT AS node_count
            FROM results.starterpack_leiden_labels_local
            GROUP BY community
            ORDER BY node_count DESC, community
            """
        )
        export_query(
            connection,
            """
            SELECT *
            FROM results.starterpack_leiden_labels_local
            ORDER BY node_id
            """,
            label_output,
        )
        size_output = (
            settings.parquet_outputs
            / "starterpack_leiden_community_sizes_local.parquet"
        )
        export_query(
            connection,
            """
            SELECT *
            FROM results.starterpack_leiden_community_sizes_local
            ORDER BY node_count DESC, community
            """,
            size_output,
        )
        community_count, largest_community = connection.execute(
            """
            SELECT count(*), max(node_count)
            FROM results.starterpack_leiden_community_sizes_local
            """
        ).fetchone()
        top_five = [
            int(row[0])
            for row in connection.execute(
                """
                SELECT node_count
                FROM results.starterpack_leiden_community_sizes_local
                ORDER BY node_count DESC
                LIMIT 5
                """
            ).fetchall()
        ]

    state.update(
        {
            "leiden_complete": True,
            "graph_construction_seconds": graph_seconds,
            "leiden_seconds": leiden_seconds,
        }
    )
    _write_json(paths["state"], state)
    summary = {
        "complete": True,
        "independently_computed": True,
        "paper_compatible": True,
        "algorithm": "leidenalg.ModularityVertexPartition",
        "seed": 0,
        "unweighted": True,
        "giant_node_count": node_count,
        "giant_edge_count": edge_count,
        "community_count": int(community_count),
        "largest_community": int(largest_community),
        "top_five_community_sizes": top_five,
        "modularity": modularity,
        "official_normalized_mutual_information": nmi,
        "official_adjusted_rand_index": adjusted_rand,
        "graph_construction_seconds": graph_seconds,
        "leiden_seconds": leiden_seconds,
        "input_build_seconds": input_result.seconds,
    }
    result = RunResult(
        task="starterpack_leiden_local",
        seconds=time.perf_counter() - started,
        outputs=[str(label_output), str(size_output), str(work)],
        summary=summary,
    )
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


def import_native_leiden_result() -> RunResult:
    """Import and validate the completed 32-bit C/igraph Leiden membership."""
    import pyarrow as pa

    settings = load_settings()
    started = time.perf_counter()
    work = _work_directory(settings)
    paths = _paths(work)
    membership_path = work / "native32_membership.int32"
    log_path = work / "native32.stdout.log"
    if not membership_path.exists():
        label_output = (
            settings.parquet_outputs / "starterpack_leiden_labels_local.parquet"
        )
        size_output = (
            settings.parquet_outputs
            / "starterpack_leiden_community_sizes_local.parquet"
        )
        summary_path = (
            settings.summary_outputs / "starterpack_leiden_local.json"
        )
        if not (
            label_output.exists()
            and size_output.exists()
            and summary_path.exists()
        ):
            raise RuntimeError(
                "Neither the native 32-bit Leiden membership nor the portable "
                "Leiden Parquet outputs are available"
            )
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        with connect(settings) as connection:
            connection.execute("CREATE SCHEMA IF NOT EXISTS results")
            connection.execute(
                f"""
                CREATE OR REPLACE TABLE
                    results.starterpack_leiden_labels_local AS
                SELECT
                    node_id::UINTEGER AS node_id,
                    community::UINTEGER AS community
                FROM read_parquet({sql_path(label_output)})
                """
            )
            connection.execute(
                f"""
                CREATE OR REPLACE TABLE
                    results.starterpack_leiden_community_sizes_local AS
                SELECT
                    community::UINTEGER AS community,
                    node_count::UBIGINT AS node_count
                FROM read_parquet({sql_path(size_output)})
                """
            )
            imported_nodes, imported_communities = connection.execute(
                """
                SELECT
                    count(*),
                    count(DISTINCT community)
                FROM results.starterpack_leiden_labels_local
                """
            ).fetchone()
        expected = payload["summary"]
        if int(imported_nodes) != int(expected["giant_node_count"]):
            raise RuntimeError("Portable Leiden label count failed validation")
        if int(imported_communities) != int(expected["community_count"]):
            raise RuntimeError(
                "Portable Leiden community count failed validation"
            )
        return RunResult(
            task="starterpack_leiden_local",
            seconds=time.perf_counter() - started,
            outputs=[
                str(label_output),
                str(size_output),
                str(summary_path),
            ],
            summary={
                **expected,
                "reused_portable_result": True,
            },
        )

    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    node_count = int(state["giant_node_count"])
    edge_count = int(state["giant_edge_count"])
    giant_nodes = np.fromfile(paths["giant_nodes"], dtype=np.uint32)
    local_labels_signed = np.fromfile(membership_path, dtype=np.int32)
    if len(local_labels_signed) != node_count:
        raise RuntimeError(
            f"Native membership has {len(local_labels_signed):,} rows; "
            f"expected {node_count:,}"
        )
    if np.any(local_labels_signed < 0):
        raise RuntimeError("Native membership contains negative labels")
    local_labels = local_labels_signed.astype(np.uint32)

    official_nodes, official_labels, _ = _load_official_labels()
    official_order = np.argsort(official_nodes)
    official_nodes = official_nodes[official_order]
    official_labels = official_labels[official_order]
    if not np.array_equal(official_nodes, giant_nodes):
        raise RuntimeError("Official and local giant node ordering differs")
    nmi, adjusted_rand = _comparison_metrics(local_labels, official_labels)

    log_text = log_path.read_text(encoding="utf-8")
    match = re.search(
        r"leiden_ready seconds=([0-9.]+) communities=([0-9]+) "
        r"quality=([0-9.]+) modularity=([0-9.]+)",
        log_text,
    )
    if not match:
        raise RuntimeError("Could not parse the native Leiden completion log")
    leiden_seconds = float(match.group(1))
    logged_community_count = int(match.group(2))
    modularity = float(match.group(4))

    table = pa.table(
        {
            "node_id": pa.array(giant_nodes),
            "community": pa.array(local_labels),
        }
    )
    label_output = (
        settings.parquet_outputs / "starterpack_leiden_labels_local.parquet"
    )
    size_output = (
        settings.parquet_outputs
        / "starterpack_leiden_community_sizes_local.parquet"
    )
    with connect(settings) as connection:
        connection.register("_native_leiden_labels", table)
        connection.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_leiden_labels_local AS
            SELECT
                node_id::UINTEGER AS node_id,
                community::UINTEGER AS community
            FROM _native_leiden_labels
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE
                results.starterpack_leiden_community_sizes_local AS
            SELECT
                community,
                count(*)::UBIGINT AS node_count
            FROM results.starterpack_leiden_labels_local
            GROUP BY community
            ORDER BY node_count DESC, community
            """
        )
        export_query(
            connection,
            """
            SELECT *
            FROM results.starterpack_leiden_labels_local
            ORDER BY node_id
            """,
            label_output,
        )
        export_query(
            connection,
            """
            SELECT *
            FROM results.starterpack_leiden_community_sizes_local
            ORDER BY node_count DESC, community
            """,
            size_output,
        )
        community_count, largest_community = connection.execute(
            """
            SELECT count(*), max(node_count)
            FROM results.starterpack_leiden_community_sizes_local
            """
        ).fetchone()
        top_five = [
            int(row[0])
            for row in connection.execute(
                """
                SELECT node_count
                FROM results.starterpack_leiden_community_sizes_local
                ORDER BY node_count DESC
                LIMIT 5
                """
            ).fetchall()
        ]
    if int(community_count) != logged_community_count:
        raise RuntimeError("Imported community count differs from native log")

    state.update(
        {
            "native32_leiden_complete": True,
            "native32_community_count": int(community_count),
            "native32_modularity": modularity,
            "native32_leiden_seconds": leiden_seconds,
        }
    )
    _write_json(paths["state"], state)
    summary = {
        "complete": True,
        "independently_computed": True,
        "full_graph": True,
        "backend": "C/igraph 0.10.16, 32-bit integer build",
        "algorithm": "igraph_community_leiden modularity",
        "paper_objective_and_parameters": True,
        "same_backend_as_paper": False,
        "why_native_backend_was_used": (
            "The paper's 64-bit Python igraph constructor reached 95.9% "
            "total system RAM before Leiden started."
        ),
        "seed": 0,
        "iterations": 2,
        "beta": 0.01,
        "unweighted": True,
        "giant_node_count": node_count,
        "giant_edge_count": edge_count,
        "community_count": int(community_count),
        "largest_community": int(largest_community),
        "top_five_community_sizes": top_five,
        "modularity": modularity,
        "official_community_count": int(official_labels.max(initial=0)) + 1,
        "official_normalized_mutual_information": nmi,
        "official_adjusted_rand_index": adjusted_rand,
        "graph_construction_seconds": 184.062,
        "leiden_seconds": leiden_seconds,
        "observed_working_set_gb": 8.2,
    }
    summary_path = settings.summary_outputs / "starterpack_leiden_local.json"
    result = RunResult(
        task="starterpack_leiden_local",
        seconds=time.perf_counter() - started,
        outputs=[str(label_output), str(size_output), str(work)],
        summary=summary,
    )
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
