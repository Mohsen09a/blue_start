from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
from numba import njit

from .duckdb_backend import connect, export_query
from .paths import project_root
from .pipeline import RunResult
from .settings import DuckDBSettings, load_settings


SCC_WORK_NAME = "follow_scc_exact"
PARTITIONS = 256


@njit(cache=True)
def fill_csr_batch(
    keys: np.ndarray,
    values: np.ndarray,
    offsets: np.ndarray,
    cursors: np.ndarray,
    neighbors: np.ndarray,
    reset_marker: np.ndarray,
    token: int,
) -> int:
    """Scatter one edge batch, resetting a retried bucket safely."""
    overflow = 0
    for index in range(len(keys)):
        key = int(keys[index])
        if reset_marker[key] != token:
            cursors[key] = offsets[key]
            reset_marker[key] = token
        position = int(cursors[key])
        if position >= offsets[key + 1]:
            overflow += 1
            continue
        neighbors[position] = values[index]
        cursors[key] = position + 1
    return overflow


@njit(cache=True)
def kosaraju_finishing_order(
    forward_offsets: np.ndarray,
    forward_neighbors: np.ndarray,
    reverse_offsets: np.ndarray,
    visited: np.ndarray,
    order: np.ndarray,
    stack_nodes: np.ndarray,
    stack_positions: np.ndarray,
) -> int:
    """Compute iterative DFS finishing order for active graph nodes."""
    order_count = 0
    node_count = len(forward_offsets) - 1

    for start in range(node_count):
        if visited[start] != 0:
            continue
        if (
            forward_offsets[start] == forward_offsets[start + 1]
            and reverse_offsets[start] == reverse_offsets[start + 1]
        ):
            continue

        visited[start] = 1
        stack_size = 1
        stack_nodes[0] = start
        stack_positions[0] = forward_offsets[start]

        while stack_size > 0:
            top = stack_size - 1
            node = int(stack_nodes[top])
            position = np.int64(stack_positions[top])
            end = np.int64(forward_offsets[node + 1])

            while position < end and visited[int(forward_neighbors[position])] != 0:
                position += 1

            if position < end:
                neighbor = int(forward_neighbors[position])
                stack_positions[top] = position + 1
                visited[neighbor] = 1
                stack_nodes[stack_size] = neighbor
                stack_positions[stack_size] = forward_offsets[neighbor]
                stack_size += 1
            else:
                order[order_count] = node
                order_count += 1
                stack_size -= 1

    return order_count


@njit(cache=True)
def kosaraju_component_sizes(
    reverse_offsets: np.ndarray,
    reverse_neighbors: np.ndarray,
    order: np.ndarray,
    order_count: int,
    visited: np.ndarray,
    stack_nodes: np.ndarray,
    component_sizes: np.ndarray,
) -> int:
    """Traverse the transpose in reverse finishing order."""
    component_count = 0

    for order_index in range(order_count - 1, -1, -1):
        start = int(order[order_index])
        if visited[start] != 0:
            continue

        visited[start] = 1
        stack_size = 1
        stack_nodes[0] = start
        size = 0

        while stack_size > 0:
            stack_size -= 1
            node = int(stack_nodes[stack_size])
            size += 1
            begin = np.int64(reverse_offsets[node])
            end = np.int64(reverse_offsets[node + 1])
            for position in range(begin, end):
                neighbor = int(reverse_neighbors[position])
                if visited[neighbor] == 0:
                    visited[neighbor] = 1
                    stack_nodes[stack_size] = neighbor
                    stack_size += 1

        component_sizes[component_count] = size
        component_count += 1

    return component_count


def _work_directory(settings: DuckDBSettings) -> Path:
    return settings.database.parent / SCC_WORK_NAME


def _safe_remove_work(path: Path, settings: DuckDBSettings) -> None:
    expected = (settings.database.parent / SCC_WORK_NAME).resolve()
    target = path.resolve()
    if target != expected:
        raise RuntimeError(f"Refusing to remove unexpected SCC path: {target}")
    if target.exists():
        shutil.rmtree(target)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_result(settings: DuckDBSettings, result: RunResult) -> RunResult:
    output = settings.summary_outputs / f"{result.task}.json"
    _write_json(
        output,
        {
            "task": result.task,
            "seconds": result.seconds,
            "outputs": result.outputs,
            "summary": result.summary,
        },
    )
    return RunResult(
        task=result.task,
        seconds=result.seconds,
        outputs=[*result.outputs, str(output)],
        summary=result.summary,
    )


def _paths(work: Path) -> dict[str, Path]:
    return {
        "state": work / "state.json",
        "forward_offsets": work / "forward_offsets.uint64",
        "reverse_offsets": work / "reverse_offsets.uint64",
        "forward_neighbors": work / "forward_neighbors.uint32",
        "reverse_neighbors": work / "reverse_neighbors.uint32",
        "forward_cursors": work / "forward_cursors.uint64",
        "reverse_cursors": work / "reverse_cursors.uint64",
        "order": work / "finishing_order.uint32",
    }


def _initial_state() -> dict[str, object]:
    return {
        "version": 1,
        "partitions": PARTITIONS,
        "offsets_complete": False,
        "forward_next_bucket": 0,
        "reverse_next_bucket": 0,
        "csr_complete": False,
        "first_pass_complete": False,
    }


def _ensure_disk_space(work: Path, minimum_free_bytes: int = 35_000_000_000) -> None:
    free = shutil.disk_usage(work.parent).free
    if free < minimum_free_bytes:
        raise RuntimeError(
            "Exact SCC needs at least 35 GB free before starting; "
            f"only {free / 1e9:.2f} GB is available"
        )


def _copy_offsets_to_cursors(
    offsets: np.memmap,
    cursor_path: Path,
    node_count: int,
) -> np.memmap:
    cursors = np.memmap(
        cursor_path,
        dtype=np.uint64,
        mode="w+",
        shape=(node_count,),
    )
    chunk = 4_000_000
    for start in range(0, node_count, chunk):
        end = min(start + chunk, node_count)
        cursors[start:end] = offsets[start:end]
    cursors.flush()
    return cursors


def _build_offsets(
    settings: DuckDBSettings,
    work: Path,
    paths: dict[str, Path],
    state: dict[str, object],
) -> tuple[int, int, int]:
    import pyarrow.parquet as pq

    degree_path = settings.parquet_outputs / "follow_degrees_full.parquet"
    if not degree_path.exists():
        raise RuntimeError("Run the full following analysis before exact SCC")

    with connect(settings, read_only=True) as connection:
        node_count, active_nodes, edge_count = connection.execute(
            """
            SELECT
                max(node_id)::UBIGINT + 1,
                count(*)::UBIGINT,
                sum(out_degree)::UBIGINT
            FROM results.follow_degrees_full
            """
        ).fetchone()
        reverse_edges = connection.execute(
            "SELECT sum(in_degree)::UBIGINT FROM results.follow_degrees_full"
        ).fetchone()[0]
    node_count = int(node_count)
    active_nodes = int(active_nodes)
    edge_count = int(edge_count)
    if edge_count != int(reverse_edges):
        raise RuntimeError("Incoming and outgoing degree totals differ")

    forward = np.memmap(
        paths["forward_offsets"],
        dtype=np.uint64,
        mode="w+",
        shape=(node_count + 1,),
    )
    reverse = np.memmap(
        paths["reverse_offsets"],
        dtype=np.uint64,
        mode="w+",
        shape=(node_count + 1,),
    )
    forward[:] = 0
    reverse[:] = 0

    parquet = pq.ParquetFile(degree_path)
    for batch in parquet.iter_batches(
        batch_size=1_000_000,
        columns=["node_id", "in_degree", "out_degree"],
        use_threads=True,
    ):
        node_ids = batch.column(0).to_numpy(zero_copy_only=False)
        in_degrees = batch.column(1).to_numpy(zero_copy_only=False)
        out_degrees = batch.column(2).to_numpy(zero_copy_only=False)
        forward[node_ids.astype(np.int64) + 1] = out_degrees
        reverse[node_ids.astype(np.int64) + 1] = in_degrees

    np.cumsum(forward, dtype=np.uint64, out=forward)
    np.cumsum(reverse, dtype=np.uint64, out=reverse)
    forward.flush()
    reverse.flush()
    if int(forward[-1]) != edge_count or int(reverse[-1]) != edge_count:
        raise RuntimeError("CSR offsets do not match the full edge count")

    state.update(
        {
            "node_count": node_count,
            "active_nodes": active_nodes,
            "edge_count": edge_count,
            "offsets_complete": True,
        }
    )
    _write_json(paths["state"], state)
    return node_count, active_nodes, edge_count


def _validate_cursors(
    cursors: np.memmap,
    offsets: np.memmap,
    node_count: int,
) -> None:
    chunk = 4_000_000
    mismatches = 0
    for start in range(0, node_count, chunk):
        end = min(start + chunk, node_count)
        mismatches += int(
            np.count_nonzero(cursors[start:end] != offsets[start + 1 : end + 1])
        )
    if mismatches:
        raise RuntimeError(f"CSR cursor validation found {mismatches} bad nodes")


def _build_direction(
    *,
    direction: str,
    work: Path,
    paths: dict[str, Path],
    state: dict[str, object],
    node_count: int,
    edge_count: int,
    maximum_new_buckets: int | None,
) -> tuple[int, list[float]]:
    import pyarrow.parquet as pq

    if direction == "forward":
        key_column, value_column = "src", "dst"
        index_name, partition_name = "by_src", "src_bucket"
    elif direction == "reverse":
        key_column, value_column = "dst", "src"
        index_name, partition_name = "by_dst", "dst_bucket"
    else:
        raise ValueError(direction)

    offsets_path = paths[f"{direction}_offsets"]
    neighbors_path = paths[f"{direction}_neighbors"]
    cursor_path = paths[f"{direction}_cursors"]
    next_key = f"{direction}_next_bucket"
    offsets = np.memmap(
        offsets_path,
        dtype=np.uint64,
        mode="r",
        shape=(node_count + 1,),
    )
    if neighbors_path.exists():
        neighbors = np.memmap(
            neighbors_path,
            dtype=np.uint32,
            mode="r+",
            shape=(edge_count,),
        )
    else:
        neighbors = np.memmap(
            neighbors_path,
            dtype=np.uint32,
            mode="w+",
            shape=(edge_count,),
        )
    if cursor_path.exists():
        cursors = np.memmap(
            cursor_path,
            dtype=np.uint64,
            mode="r+",
            shape=(node_count,),
        )
    else:
        cursors = _copy_offsets_to_cursors(offsets, cursor_path, node_count)

    reset_marker = np.zeros(node_count, dtype=np.uint16)
    next_bucket = int(state[next_key])
    timings: list[float] = []
    built = 0
    index_root = work.parent / "follow_indexes" / index_name

    # Compile before timing the real data.
    probe_offsets = np.array([0, 1, 1], dtype=np.uint64)
    probe_cursors = np.array([0, 1], dtype=np.uint64)
    probe_neighbors = np.zeros(1, dtype=np.uint32)
    probe_marker = np.zeros(2, dtype=np.uint16)
    fill_csr_batch(
        np.array([0], dtype=np.uint32),
        np.array([1], dtype=np.uint32),
        probe_offsets,
        probe_cursors,
        probe_neighbors,
        probe_marker,
        1,
    )

    for bucket in range(next_bucket, PARTITIONS):
        if maximum_new_buckets is not None and built >= maximum_new_buckets:
            break
        bucket_path = (
            index_root
            / f"{partition_name}={bucket}"
            / "data_0.parquet"
        )
        if not bucket_path.exists():
            raise RuntimeError(f"Missing follow index partition: {bucket_path}")

        started = time.perf_counter()
        overflow = 0
        rows = 0
        parquet = pq.ParquetFile(bucket_path)
        token = bucket + 1
        for batch in parquet.iter_batches(
            batch_size=1_000_000,
            columns=[key_column, value_column],
            use_threads=True,
        ):
            keys = batch.column(0).to_numpy(zero_copy_only=False)
            values = batch.column(1).to_numpy(zero_copy_only=False)
            overflow += fill_csr_batch(
                keys,
                values,
                offsets,
                cursors,
                neighbors,
                reset_marker,
                token,
            )
            rows += len(keys)
        if overflow:
            raise RuntimeError(
                f"{direction} bucket {bucket} overflowed {overflow} CSR slots"
            )
        neighbors.flush()
        cursors.flush()
        elapsed = time.perf_counter() - started
        timings.append(elapsed)
        state[next_key] = bucket + 1
        _write_json(paths["state"], state)
        built += 1
        print(
            f"[{direction.upper()}] bucket {bucket + 1}/{PARTITIONS}: "
            f"{rows:,} edges in {elapsed:.2f} seconds",
            flush=True,
        )

    if int(state[next_key]) == PARTITIONS:
        _validate_cursors(cursors, offsets, node_count)
        del cursors
        cursor_path.unlink(missing_ok=True)
    del neighbors, offsets, reset_marker
    return built, timings


def _run_kosaraju(
    *,
    paths: dict[str, Path],
    state: dict[str, object],
    node_count: int,
    active_nodes: int,
    edge_count: int,
) -> np.ndarray:
    forward_offsets = np.memmap(
        paths["forward_offsets"],
        dtype=np.uint64,
        mode="r",
        shape=(node_count + 1,),
    )
    reverse_offsets = np.memmap(
        paths["reverse_offsets"],
        dtype=np.uint64,
        mode="r",
        shape=(node_count + 1,),
    )
    forward_neighbors = np.memmap(
        paths["forward_neighbors"],
        dtype=np.uint32,
        mode="r",
        shape=(edge_count,),
    )
    reverse_neighbors = np.memmap(
        paths["reverse_neighbors"],
        dtype=np.uint32,
        mode="r",
        shape=(edge_count,),
    )
    order = np.memmap(
        paths["order"],
        dtype=np.uint32,
        mode="w+",
        shape=(active_nodes,),
    )
    visited = np.zeros(node_count, dtype=np.uint8)
    stack_nodes = np.empty(active_nodes, dtype=np.uint32)
    stack_positions = np.empty(active_nodes, dtype=np.uint64)

    # Compile both traversal kernels on a small directed graph.
    probe_offsets = np.array([0, 1, 2, 2], dtype=np.uint64)
    probe_reverse_offsets = np.array([0, 0, 1, 2], dtype=np.uint64)
    probe_neighbors = np.array([1, 2], dtype=np.uint32)
    probe_reverse_neighbors = np.array([0, 1], dtype=np.uint32)
    probe_visited = np.zeros(3, dtype=np.uint8)
    probe_order = np.empty(3, dtype=np.uint32)
    probe_stack = np.empty(3, dtype=np.uint32)
    probe_positions = np.empty(3, dtype=np.uint64)
    probe_count = kosaraju_finishing_order(
        probe_offsets,
        probe_neighbors,
        probe_reverse_offsets,
        probe_visited,
        probe_order,
        probe_stack,
        probe_positions,
    )
    probe_visited.fill(0)
    probe_sizes = np.empty(3, dtype=np.uint32)
    kosaraju_component_sizes(
        probe_reverse_offsets,
        probe_reverse_neighbors,
        probe_order,
        probe_count,
        probe_visited,
        probe_stack,
        probe_sizes,
    )

    print("[SCC] Computing forward DFS finishing order...", flush=True)
    first_started = time.perf_counter()
    order_count = kosaraju_finishing_order(
        forward_offsets,
        forward_neighbors,
        reverse_offsets,
        visited,
        order,
        stack_nodes,
        stack_positions,
    )
    if order_count != active_nodes:
        raise RuntimeError(
            f"Finishing order contains {order_count} nodes, "
            f"expected {active_nodes}"
        )
    order.flush()
    state["first_pass_complete"] = True
    state["first_pass_seconds"] = time.perf_counter() - first_started
    _write_json(paths["state"], state)

    print("[SCC] Traversing the reverse graph...", flush=True)
    visited.fill(0)
    component_sizes = np.empty(active_nodes, dtype=np.uint32)
    second_started = time.perf_counter()
    component_count = kosaraju_component_sizes(
        reverse_offsets,
        reverse_neighbors,
        order,
        order_count,
        visited,
        stack_nodes,
        component_sizes,
    )
    state["second_pass_seconds"] = time.perf_counter() - second_started
    state["component_count"] = component_count
    _write_json(paths["state"], state)
    return component_sizes[:component_count].copy()


def compute_follow_scc_exact(
    *,
    maximum_new_buckets: int | None = None,
    rebuild: bool = False,
) -> RunResult:
    """Compute exact SCCs using checkpointed disk-backed CSR arrays."""
    if maximum_new_buckets is not None and maximum_new_buckets < 1:
        raise ValueError("maximum_new_buckets must be positive")

    settings = load_settings()
    started = time.perf_counter()
    work = _work_directory(settings)
    paths = _paths(work)
    if rebuild:
        _safe_remove_work(work, settings)
    work.mkdir(parents=True, exist_ok=True)
    _ensure_disk_space(work)

    if paths["state"].exists():
        state = json.loads(paths["state"].read_text(encoding="utf-8"))
    else:
        state = _initial_state()
        _write_json(paths["state"], state)

    cached_summary_path = settings.summary_outputs / "follow_scc_exact.json"
    if (
        not rebuild
        and bool(state.get("complete"))
        and cached_summary_path.exists()
    ):
        cached = json.loads(cached_summary_path.read_text(encoding="utf-8"))
        summary = {**cached["summary"], "reused_completed_result": True}
        return RunResult(
            task="follow_scc_exact",
            seconds=time.perf_counter() - started,
            outputs=[*cached["outputs"], str(cached_summary_path)],
            summary=summary,
        )

    if not bool(state["offsets_complete"]):
        node_count, active_nodes, edge_count = _build_offsets(
            settings,
            work,
            paths,
            state,
        )
    else:
        node_count = int(state["node_count"])
        active_nodes = int(state["active_nodes"])
        edge_count = int(state["edge_count"])

    remaining_budget = maximum_new_buckets
    built_forward, forward_times = _build_direction(
        direction="forward",
        work=work,
        paths=paths,
        state=state,
        node_count=node_count,
        edge_count=edge_count,
        maximum_new_buckets=remaining_budget,
    )
    if remaining_budget is not None:
        remaining_budget -= built_forward

    built_reverse = 0
    reverse_times: list[float] = []
    if int(state["forward_next_bucket"]) == PARTITIONS:
        built_reverse, reverse_times = _build_direction(
            direction="reverse",
            work=work,
            paths=paths,
            state=state,
            node_count=node_count,
            edge_count=edge_count,
            maximum_new_buckets=remaining_budget,
        )

    csr_complete = (
        int(state["forward_next_bucket"]) == PARTITIONS
        and int(state["reverse_next_bucket"]) == PARTITIONS
    )
    state["csr_complete"] = csr_complete
    _write_json(paths["state"], state)

    if not csr_complete:
        all_times = forward_times + reverse_times
        summary = {
            "complete": False,
            "safe_probe": maximum_new_buckets is not None,
            "node_count": node_count,
            "active_nodes": active_nodes,
            "edge_count": edge_count,
            "forward_next_bucket": int(state["forward_next_bucket"]),
            "reverse_next_bucket": int(state["reverse_next_bucket"]),
            "new_buckets_built": built_forward + built_reverse,
            "mean_seconds_per_new_bucket": (
                float(np.mean(all_times)) if all_times else None
            ),
        }
        return _write_result(
            settings,
            RunResult(
                task="follow_scc_exact_probe",
                seconds=time.perf_counter() - started,
                outputs=[str(work), str(paths["state"])],
                summary=summary,
            ),
        )

    component_sizes = _run_kosaraju(
        paths=paths,
        state=state,
        node_count=node_count,
        active_nodes=active_nodes,
        edge_count=edge_count,
    )
    distribution = np.bincount(component_sizes.astype(np.int64))
    nonzero_sizes = np.flatnonzero(distribution)

    import pyarrow as pa

    table = pa.table(
        {
            "component_size": pa.array(
                nonzero_sizes.astype(np.uint32)
            ),
            "component_count": pa.array(
                distribution[nonzero_sizes].astype(np.uint64)
            ),
        }
    )
    with connect(settings) as connection:
        connection.register("_local_scc_distribution", table)
        connection.execute(
            """
            CREATE OR REPLACE TABLE results.follow_scc_distribution_local AS
            SELECT
                component_size::UINTEGER AS component_size,
                component_count::UBIGINT AS component_count
            FROM _local_scc_distribution
            ORDER BY component_size
            """
        )
        output = settings.parquet_outputs / "follow_scc_distribution_local.parquet"
        export_query(
            connection,
            """
            SELECT *
            FROM results.follow_scc_distribution_local
            ORDER BY component_size
            """,
            output,
        )
        differing_rows = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT
                    coalesce(local.component_size, official.component_size)
                        AS component_size,
                    coalesce(local.component_count, 0) AS local_count,
                    coalesce(official.component_count, 0) AS official_count
                FROM results.follow_scc_distribution_local AS local
                FULL OUTER JOIN results.reference_follow_scc_distribution
                    AS official USING (component_size)
                WHERE coalesce(local.component_count, 0)
                      != coalesce(official.component_count, 0)
            )
            """
        ).fetchone()[0]

    state["complete"] = True
    state["differing_reference_rows"] = int(differing_rows)
    _write_json(paths["state"], state)
    summary = {
        "complete": True,
        "exact": True,
        "disk_backed_csr": True,
        "node_count": node_count,
        "active_nodes": active_nodes,
        "edge_count": edge_count,
        "component_count": int(len(component_sizes)),
        "largest_component": int(component_sizes.max(initial=0)),
        "official_distribution_differing_rows": int(differing_rows),
        "official_distribution_exact_match": differing_rows == 0,
        "work_directory": str(work),
    }
    return _write_result(
        settings,
        RunResult(
            task="follow_scc_exact",
            seconds=time.perf_counter() - started,
            outputs=[str(output), str(work)],
            summary=summary,
        ),
    )
