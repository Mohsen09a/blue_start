from __future__ import annotations

import json
import math
import random
import shutil
import time
from pathlib import Path

import numpy as np
from numba import njit

from .duckdb_backend import connect, export_query, sql_path
from .pipeline import RunResult
from .settings import DuckDBSettings, load_settings


@njit(cache=True)
def _uf_find(parent: np.ndarray, node: int) -> int:
    root = node
    while parent[root] != root:
        root = int(parent[root])
    while parent[node] != node:
        next_node = int(parent[node])
        parent[node] = root
        node = next_node
    return root


@njit(cache=True)
def union_edge_batch(
    parent: np.ndarray,
    sizes: np.ndarray,
    seen: np.ndarray,
    sources: np.ndarray,
    destinations: np.ndarray,
) -> None:
    """Union a batch of undirected edges using compact integer arrays."""
    for index in range(len(sources)):
        left = int(sources[index])
        right = int(destinations[index])
        seen[left] = 1
        seen[right] = 1
        root_left = _uf_find(parent, left)
        root_right = _uf_find(parent, right)
        if root_left == root_right:
            continue
        if sizes[root_left] < sizes[root_right]:
            root_left, root_right = root_right, root_left
        parent[root_right] = root_left
        sizes[root_left] += sizes[root_right]


@njit(cache=True)
def compress_seen_paths(parent: np.ndarray, seen: np.ndarray) -> None:
    for node in range(len(parent)):
        if seen[node]:
            parent[node] = _uf_find(parent, node)


def _write_result_summary(
    settings: DuckDBSettings,
    result: RunResult,
) -> RunResult:
    summary_path = settings.summary_outputs / f"{result.task}.json"
    summary_path.write_text(
        json.dumps(
            {
                "task": result.task,
                "seconds": result.seconds,
                "outputs": result.outputs,
                "summary": result.summary,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return RunResult(
        result.task,
        result.seconds,
        result.outputs + [str(summary_path)],
        result.summary,
    )


def compute_follow_wcc(*, batch_size: int = 4_000_000) -> RunResult:
    """Compute exact WCC sizes for the complete follow graph."""
    if batch_size < 100_000:
        raise ValueError("batch_size must be at least 100,000")

    settings = load_settings()
    started = time.perf_counter()

    with connect(settings) as connection:
        max_node_id = int(
            connection.execute("SELECT max(node_id) FROM nodes").fetchone()[0]
        )
        slot_count = max_node_id + 1
        parent = np.arange(slot_count, dtype=np.uint32)
        sizes = np.ones(slot_count, dtype=np.uint32)
        seen = np.zeros(slot_count, dtype=np.uint8)

        # Compile the Numba kernels before starting the multi-billion-row scan.
        probe_parent = np.arange(2, dtype=np.uint32)
        probe_size = np.ones(2, dtype=np.uint32)
        probe_seen = np.zeros(2, dtype=np.uint8)
        union_edge_batch(
            probe_parent,
            probe_size,
            probe_seen,
            np.array([0], dtype=np.uint32),
            np.array([1], dtype=np.uint32),
        )

        reader = connection.execute(
            "SELECT src, dst FROM follows"
        ).fetch_record_batch(rows_per_batch=batch_size)
        edge_count = 0
        batch_count = 0
        for batch in reader:
            sources = batch.column(0).to_numpy(zero_copy_only=False)
            destinations = batch.column(1).to_numpy(zero_copy_only=False)
            if (
                int(sources.max(initial=0)) >= slot_count
                or int(destinations.max(initial=0)) >= slot_count
            ):
                raise ValueError("A follow endpoint is missing from the node table")
            union_edge_batch(parent, sizes, seen, sources, destinations)
            edge_count += len(sources)
            batch_count += 1
            if batch_count % 25 == 0:
                print(f"Processed {edge_count:,} follow edges", flush=True)

        compress_seen_paths(parent, seen)
        seen_nodes = np.flatnonzero(seen)
        roots = parent[seen_nodes]
        root_counts = np.bincount(roots, minlength=slot_count)
        component_sizes = root_counts[root_counts > 0]
        size_values, component_counts = np.unique(
            component_sizes,
            return_counts=True,
        )

        rows = [
            (int(size), int(count))
            for size, count in zip(size_values, component_counts, strict=True)
        ]
        connection.execute(
            """
            CREATE OR REPLACE TABLE results.follow_wcc_distribution_local (
                component_size UINTEGER,
                component_count UBIGINT
            )
            """
        )
        connection.executemany(
            "INSERT INTO results.follow_wcc_distribution_local VALUES (?, ?)",
            rows,
        )
        output = settings.parquet_outputs / "follow_wcc_distribution_local.parquet"
        export_query(
            connection,
            """
            SELECT *
            FROM results.follow_wcc_distribution_local
            ORDER BY component_size
            """,
            output,
        )

    summary = {
        "exact": True,
        "edge_count": edge_count,
        "follow_network_nodes": int(len(seen_nodes)),
        "component_count": int(len(component_sizes)),
        "largest_component_size": int(component_sizes.max(initial=0)),
        "batch_size": batch_size,
    }
    result = RunResult(
        task="follow_wcc_local",
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary=summary,
    )
    return _write_result_summary(settings, result)


def _build_hypergraph_arrays(
    connection: object,
    *,
    selected_pack_table: str | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return pack IDs, node IDs, edge offsets, and dense edge memberships."""
    if selected_pack_table is None:
        pack_query = "SELECT pack_id FROM starterpacks ORDER BY pack_id"
        membership_query = """
            SELECT DISTINCT pack_id, member_id
            FROM starterpack_memberships
            ORDER BY pack_id, member_id
        """
    else:
        pack_query = f"""
            SELECT pack_id
            FROM {selected_pack_table}
            ORDER BY pack_id
        """
        membership_query = f"""
            SELECT DISTINCT m.pack_id, m.member_id
            FROM starterpack_memberships AS m
            JOIN {selected_pack_table} AS selected USING (pack_id)
            ORDER BY m.pack_id, m.member_id
        """

    pack_ids = (
        connection.execute(pack_query)
        .fetchnumpy()["pack_id"]
        .astype(np.uint32, copy=False)
    )
    membership = connection.execute(membership_query).fetchnumpy()
    membership_pack_ids = membership["pack_id"].astype(np.uint32, copy=False)
    membership_node_ids = membership["member_id"].astype(np.uint32, copy=False)

    edge_indices = np.searchsorted(pack_ids, membership_pack_ids)
    if len(edge_indices) and not np.array_equal(
        pack_ids[edge_indices],
        membership_pack_ids,
    ):
        raise ValueError("Membership references an unknown Starter Pack")

    edge_counts = np.bincount(edge_indices, minlength=len(pack_ids))
    edge_offsets = np.empty(len(pack_ids) + 1, dtype=np.int64)
    edge_offsets[0] = 0
    np.cumsum(edge_counts, out=edge_offsets[1:])

    node_ids = np.unique(membership_node_ids)
    if len(node_ids):
        direct_map = np.full(int(node_ids[-1]) + 1, -1, dtype=np.int32)
        direct_map[node_ids] = np.arange(len(node_ids), dtype=np.int32)
        edge_nodes = direct_map[membership_node_ids].astype(np.uint32, copy=False)
        del direct_map
    else:
        edge_nodes = np.empty(0, dtype=np.uint32)

    return pack_ids, node_ids, edge_offsets, edge_nodes


@njit(cache=True)
def _build_node_to_edge_csr(
    edge_offsets: np.ndarray,
    edge_nodes: np.ndarray,
    node_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    degrees = np.zeros(node_count, dtype=np.int32)
    for node in edge_nodes:
        degrees[int(node)] += 1

    node_offsets = np.empty(node_count + 1, dtype=np.int64)
    node_offsets[0] = 0
    for node in range(node_count):
        node_offsets[node + 1] = node_offsets[node] + degrees[node]

    cursor = node_offsets[:-1].copy()
    node_edges = np.empty(len(edge_nodes), dtype=np.uint32)
    for edge in range(len(edge_offsets) - 1):
        for position in range(edge_offsets[edge], edge_offsets[edge + 1]):
            node = int(edge_nodes[position])
            location = cursor[node]
            node_edges[location] = edge
            cursor[node] += 1
    return node_offsets, node_edges


@njit(cache=True)
def _heap_less(
    heap_nodes: np.ndarray,
    heap_degrees: np.ndarray,
    left: int,
    right: int,
) -> bool:
    if heap_degrees[left] != heap_degrees[right]:
        return heap_degrees[left] < heap_degrees[right]
    return heap_nodes[left] < heap_nodes[right]


@njit(cache=True)
def _heap_sift_down(
    heap_nodes: np.ndarray,
    heap_degrees: np.ndarray,
    heap_size: int,
    position: int,
) -> None:
    while True:
        left = 2 * position + 1
        if left >= heap_size:
            return
        right = left + 1
        child = left
        if right < heap_size and _heap_less(
            heap_nodes,
            heap_degrees,
            right,
            left,
        ):
            child = right
        if not _heap_less(
            heap_nodes,
            heap_degrees,
            child,
            position,
        ):
            return
        heap_degrees[position], heap_degrees[child] = (
            heap_degrees[child],
            heap_degrees[position],
        )
        heap_nodes[position], heap_nodes[child] = (
            heap_nodes[child],
            heap_nodes[position],
        )
        position = child


@njit(cache=True)
def hypergraph_core_numbers(
    edge_offsets: np.ndarray,
    edge_nodes: np.ndarray,
    node_offsets: np.ndarray,
    node_edges: np.ndarray,
) -> np.ndarray:
    """Exact array-based port of the paper's hypergraph peeling algorithm."""
    node_count = len(node_offsets) - 1
    edge_count = len(edge_offsets) - 1
    degrees = np.empty(node_count, dtype=np.int32)
    for node in range(node_count):
        degrees[node] = node_offsets[node + 1] - node_offsets[node]

    edge_remaining = np.empty(edge_count, dtype=np.int32)
    edge_active = np.ones(edge_count, dtype=np.uint8)
    for edge in range(edge_count):
        edge_remaining[edge] = edge_offsets[edge + 1] - edge_offsets[edge]

    active = np.ones(node_count, dtype=np.uint8)
    core = np.zeros(node_count, dtype=np.int32)

    capacity = node_count + edge_count + 1
    heap_nodes = np.empty(capacity, dtype=np.uint32)
    heap_degrees = np.empty(capacity, dtype=np.int32)
    for node in range(node_count):
        heap_nodes[node] = node
        heap_degrees[node] = degrees[node]
    heap_size = node_count
    for position in range(heap_size // 2 - 1, -1, -1):
        _heap_sift_down(heap_nodes, heap_degrees, heap_size, position)

    removed_count = 0
    while heap_size:
        node = int(heap_nodes[0])
        candidate_degree = int(heap_degrees[0])
        heap_size -= 1
        if heap_size:
            heap_nodes[0] = heap_nodes[heap_size]
            heap_degrees[0] = heap_degrees[heap_size]
            _heap_sift_down(heap_nodes, heap_degrees, heap_size, 0)

        if not active[node] or candidate_degree != degrees[node]:
            continue

        active[node] = 0
        core[node] = candidate_degree
        removed_count += 1

        for location in range(node_offsets[node], node_offsets[node + 1]):
            edge = int(node_edges[location])
            if not edge_active[edge]:
                continue
            edge_remaining[edge] -= 1
            if edge_remaining[edge] >= 2:
                continue

            edge_active[edge] = 0
            if edge_remaining[edge] != 1:
                continue

            remaining_node = -1
            for position in range(edge_offsets[edge], edge_offsets[edge + 1]):
                candidate = int(edge_nodes[position])
                if active[candidate]:
                    remaining_node = candidate
                    break
            if remaining_node < 0:
                continue

            degrees[remaining_node] -= 1
            insert = heap_size
            heap_size += 1
            heap_nodes[insert] = remaining_node
            heap_degrees[insert] = degrees[remaining_node]
            while insert > 0:
                parent_position = (insert - 1) // 2
                if not _heap_less(
                    heap_nodes,
                    heap_degrees,
                    insert,
                    parent_position,
                ):
                    break
                heap_degrees[parent_position], heap_degrees[insert] = (
                    heap_degrees[insert],
                    heap_degrees[parent_position],
                )
                heap_nodes[parent_position], heap_nodes[insert] = (
                    heap_nodes[insert],
                    heap_nodes[parent_position],
                )
                insert = parent_position

    if removed_count != node_count:
        raise RuntimeError("The k-core peeling process did not remove every node")
    return core


def compute_hypergraph_kcore_compact() -> RunResult:
    settings = load_settings()
    started = time.perf_counter()

    with connect(settings) as connection:
        pack_ids, node_ids, edge_offsets, edge_nodes = _build_hypergraph_arrays(
            connection
        )
        node_offsets, node_edges = _build_node_to_edge_csr(
            edge_offsets,
            edge_nodes,
            len(node_ids),
        )
        core = hypergraph_core_numbers(
            edge_offsets,
            edge_nodes,
            node_offsets,
            node_edges,
        )

        import pyarrow as pa

        core_table = pa.table(
            {
                "node_id": pa.array(node_ids),
                "core_number": pa.array(core.astype(np.uint32)),
            }
        )
        connection.register("_local_kcore", core_table)
        connection.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_kcore_local AS
            SELECT
                node_id::UINTEGER AS node_id,
                core_number::UINTEGER AS core_number
            FROM _local_kcore
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_kcore_distribution_local AS
            SELECT core_number, count(*)::UBIGINT AS node_count
            FROM results.starterpack_kcore_local
            GROUP BY core_number
            ORDER BY core_number
            """
        )
        node_output = settings.parquet_outputs / "starterpack_kcore_local.parquet"
        distribution_output = (
            settings.parquet_outputs
            / "starterpack_kcore_distribution_local.parquet"
        )
        export_query(
            connection,
            "SELECT * FROM results.starterpack_kcore_local ORDER BY node_id",
            node_output,
        )
        export_query(
            connection,
            """
            SELECT *
            FROM results.starterpack_kcore_distribution_local
            ORDER BY core_number
            """,
            distribution_output,
        )

    values, counts = np.unique(core, return_counts=True)
    summary = {
        "exact": True,
        "node_count": int(len(node_ids)),
        "pack_count": int(len(pack_ids)),
        "incidence_count": int(len(edge_nodes)),
        "maximum_core": int(core.max(initial=0)),
        "nodes_with_core_at_least_1000": int(np.count_nonzero(core >= 1000)),
        "distribution_rows": int(len(values)),
    }
    result = RunResult(
        task="starterpack_kcore_local",
        seconds=time.perf_counter() - started,
        outputs=[str(node_output), str(distribution_output)],
        summary=summary,
    )
    return _write_result_summary(settings, result)


def _load_official_labels() -> tuple[np.ndarray, np.ndarray, Path]:
    path = (
        Path(__file__).resolve().parents[2]
        / "reference"
        / "upstream-a-blue-start"
        / "data"
        / "node_labels.json"
    )
    if not path.exists():
        raise FileNotFoundError(
            "Official node labels are missing. Run the reference import first."
        )
    with path.open("r", encoding="utf-8") as handle:
        labels_dict = json.load(handle)
    node_ids = np.fromiter(
        (int(node_id) for node_id in labels_dict.keys()),
        dtype=np.uint32,
        count=len(labels_dict),
    )
    communities = np.fromiter(
        labels_dict.values(),
        dtype=np.uint16,
        count=len(labels_dict),
    )
    return node_ids, communities, path


def _register_official_labels(connection: object) -> tuple[int, int, Path]:
    import pyarrow as pa

    node_ids, communities, path = _load_official_labels()
    table = pa.table(
        {
            "node_id": pa.array(node_ids),
            "community": pa.array(communities),
        }
    )
    connection.register("_official_node_labels", table)
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE official_node_labels AS
        SELECT
            node_id::UINTEGER AS node_id,
            community::USMALLINT AS community
        FROM _official_node_labels
        """
    )
    return len(node_ids), int(communities.max(initial=0)) + 1, path


def _register_analysis_labels(
    connection: object,
    label_source: str,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Register either published or independently computed Leiden labels."""
    if label_source == "official":
        node_ids, communities, path = _load_official_labels()
        source = str(path)
    elif label_source == "independent":
        exists = connection.execute(
            """
            SELECT count(*) > 0
            FROM information_schema.tables
            WHERE table_schema = 'results'
              AND table_name = 'starterpack_leiden_labels_local'
            """
        ).fetchone()[0]
        if not exists:
            raise RuntimeError(
                "Independent Leiden labels are missing. Run "
                "'starterpack-leiden --import-native' first."
            )
        values = connection.execute(
            """
            SELECT node_id, community
            FROM results.starterpack_leiden_labels_local
            ORDER BY node_id
            """
        ).fetchnumpy()
        node_ids = values["node_id"].astype(np.uint32, copy=False)
        communities = values["community"].astype(np.uint32, copy=False)
        source = "results.starterpack_leiden_labels_local"
    else:
        raise ValueError("label_source must be 'official' or 'independent'")

    import pyarrow as pa

    table = pa.table(
        {
            "node_id": pa.array(node_ids),
            "community": pa.array(communities),
        }
    )
    connection.register("_analysis_node_labels", table)
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE analysis_node_labels AS
        SELECT
            node_id::UINTEGER AS node_id,
            community::UINTEGER AS community
        FROM _analysis_node_labels
        """
    )
    return node_ids, communities, source


def compute_edge_entropy(*, label_source: str = "official") -> RunResult:
    """Recompute Starter Pack entropy using the selected Leiden labels."""
    settings = load_settings()
    started = time.perf_counter()
    suffix = "independent" if label_source == "independent" else "local"
    table_name = f"starterpack_edge_entropy_{suffix}"

    with connect(settings) as connection:
        label_nodes, label_values, labels_source = _register_analysis_labels(
            connection,
            label_source,
        )
        label_count = len(label_nodes)
        community_count = int(label_values.max(initial=0)) + 1
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE results.{table_name} AS
            WITH unique_memberships AS (
                SELECT DISTINCT pack_id, member_id
                FROM starterpack_memberships
            ),
            pack_coverage AS (
                SELECT
                    m.pack_id,
                    count(*)::UINTEGER AS member_count,
                    count(labels.node_id)::UINTEGER AS labeled_members
                FROM unique_memberships AS m
                LEFT JOIN analysis_node_labels AS labels
                       ON labels.node_id = m.member_id
                GROUP BY m.pack_id
            ),
            eligible AS (
                SELECT pack_id, member_count
                FROM pack_coverage
                WHERE member_count = labeled_members
                  AND member_count > 0
            ),
            community_counts AS (
                SELECT
                    m.pack_id,
                    labels.community,
                    count(*)::DOUBLE AS members_in_community
                FROM unique_memberships AS m
                JOIN eligible AS e USING (pack_id)
                JOIN analysis_node_labels AS labels
                  ON labels.node_id = m.member_id
                GROUP BY m.pack_id, labels.community
            ),
            entropy_parts AS (
                SELECT
                    c.pack_id,
                    e.member_count,
                    count(*)::UINTEGER AS communities_present,
                    -sum(
                        (c.members_in_community / e.member_count)
                        * ln(c.members_in_community / e.member_count)
                    ) AS entropy_nats
                FROM community_counts AS c
                JOIN eligible AS e USING (pack_id)
                GROUP BY c.pack_id, e.member_count
            )
            SELECT
                pack_id,
                member_count,
                communities_present,
                CASE
                    WHEN member_count <= 1 OR communities_present <= 1 THEN 0.0
                    ELSE entropy_nats / ln(member_count)
                END::DOUBLE AS normalized_entropy
            FROM entropy_parts
            ORDER BY pack_id
            """
        )
        output = settings.parquet_outputs / f"{table_name}.parquet"
        export_query(
            connection,
            f"SELECT * FROM results.{table_name} ORDER BY pack_id",
            output,
        )
        (
            pack_count,
            mean_entropy,
            zero_entropy_count,
            maximum_entropy,
        ) = connection.execute(
            f"""
            SELECT
                count(*),
                avg(normalized_entropy),
                count(*) FILTER (WHERE normalized_entropy = 0),
                max(normalized_entropy)
            FROM results.{table_name}
            """
        ).fetchone()

    summary = {
        "independently_recomputed": True,
        "community_labels_source": labels_source,
        "label_source": label_source,
        "labeled_node_count": label_count,
        "community_count": community_count,
        "fully_labeled_pack_count": pack_count,
        "mean_normalized_entropy": mean_entropy,
        "zero_entropy_pack_count": zero_entropy_count,
        "zero_entropy_fraction": zero_entropy_count / pack_count,
        "maximum_normalized_entropy": maximum_entropy,
    }
    result = RunResult(
        task=table_name,
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary=summary,
    )
    return _write_result_summary(settings, result)


@njit(cache=True)
def random_edge_shuffles(
    edge_offsets: np.ndarray,
    edge_nodes: np.ndarray,
    node_count: int,
    attempts: int,
    seed: int,
) -> None:
    """Packed-array equivalent of XGI random_edge_shuffle."""
    edge_count = len(edge_offsets) - 1
    if edge_count < 2:
        return

    maximum_size = 0
    for edge in range(edge_count):
        size = edge_offsets[edge + 1] - edge_offsets[edge]
        if size > maximum_size:
            maximum_size = size

    marker = np.zeros(node_count, dtype=np.int32)
    shared_nodes = np.empty(maximum_size, dtype=np.uint32)
    exclusive_nodes = np.empty(2 * maximum_size, dtype=np.uint32)
    np.random.seed(seed)

    for attempt in range(attempts):
        edge_one = np.random.randint(0, edge_count)
        edge_two = np.random.randint(0, edge_count - 1)
        if edge_two >= edge_one:
            edge_two += 1

        start_one = edge_offsets[edge_one]
        end_one = edge_offsets[edge_one + 1]
        start_two = edge_offsets[edge_two]
        end_two = edge_offsets[edge_two + 1]
        token = attempt + 1

        for position in range(start_one, end_one):
            marker[int(edge_nodes[position])] = token

        shared_count = 0
        for position in range(start_two, end_two):
            node = int(edge_nodes[position])
            if marker[node] == token:
                marker[node] = -token
                shared_nodes[shared_count] = node
                shared_count += 1

        exclusive_count = 0
        for position in range(start_one, end_one):
            node = int(edge_nodes[position])
            if marker[node] == token:
                exclusive_nodes[exclusive_count] = node
                exclusive_count += 1
        for position in range(start_two, end_two):
            node = int(edge_nodes[position])
            if marker[node] != -token:
                exclusive_nodes[exclusive_count] = node
                exclusive_count += 1

        for position in range(exclusive_count - 1, 0, -1):
            swap_position = np.random.randint(0, position + 1)
            exclusive_nodes[position], exclusive_nodes[swap_position] = (
                exclusive_nodes[swap_position],
                exclusive_nodes[position],
            )

        exclusive_for_one = (end_one - start_one) - shared_count
        write = start_one
        for index in range(shared_count):
            edge_nodes[write] = shared_nodes[index]
            write += 1
        for index in range(exclusive_for_one):
            edge_nodes[write] = exclusive_nodes[index]
            write += 1

        write = start_two
        for index in range(shared_count):
            edge_nodes[write] = shared_nodes[index]
            write += 1
        for index in range(exclusive_for_one, exclusive_count):
            edge_nodes[write] = exclusive_nodes[index]
            write += 1


@njit(cache=True)
def normalized_edge_entropies(
    edge_offsets: np.ndarray,
    edge_nodes: np.ndarray,
    node_communities: np.ndarray,
    community_count: int,
) -> np.ndarray:
    result = np.zeros(len(edge_offsets) - 1, dtype=np.float64)
    counts = np.zeros(community_count, dtype=np.int32)
    touched = np.empty(community_count, dtype=np.int32)

    for edge in range(len(result)):
        start = edge_offsets[edge]
        end = edge_offsets[edge + 1]
        size = end - start
        if size <= 1:
            continue

        touched_count = 0
        for position in range(start, end):
            community = int(node_communities[int(edge_nodes[position])])
            if counts[community] == 0:
                touched[touched_count] = community
                touched_count += 1
            counts[community] += 1

        if touched_count > 1:
            entropy = 0.0
            for index in range(touched_count):
                community = touched[index]
                probability = counts[community] / size
                entropy -= probability * math.log(probability)
            result[edge] = entropy / math.log(size)

        for index in range(touched_count):
            counts[touched[index]] = 0
    return result


def compute_configuration_model(
    *,
    swaps_per_edge: int = 10,
    seed: int = 0,
    label_source: str = "official",
) -> RunResult:
    """Run the paper's degree- and edge-size-preserving randomization."""
    if swaps_per_edge < 1:
        raise ValueError("swaps_per_edge must be positive")

    settings = load_settings()
    started = time.perf_counter()
    suffix = "independent" if label_source == "independent" else "local"
    entropy_table_name = f"starterpack_edge_entropy_{suffix}"
    output_table_name = f"starterpack_configuration_entropy_{suffix}"

    with connect(settings) as connection:
        label_nodes, label_values, labels_source = _register_analysis_labels(
            connection,
            label_source,
        )
        label_count = len(label_nodes)
        community_count = int(label_values.max(initial=0)) + 1
        if not connection.execute(
            f"""
            SELECT count(*) > 0
            FROM information_schema.tables
            WHERE table_schema = 'results'
              AND table_name = '{entropy_table_name}'
            """
        ).fetchone()[0]:
            raise RuntimeError(
                f"Run edge-entropy --label-source {label_source} before "
                "configuration-model"
            )

        pack_ids, node_ids, edge_offsets, edge_nodes = _build_hypergraph_arrays(
            connection,
            selected_pack_table=f"results.{entropy_table_name}",
        )

        label_map = np.full(int(label_nodes.max(initial=0)) + 1, -1, dtype=np.int32)
        label_map[label_nodes] = label_values.astype(np.int32)
        node_communities = label_map[node_ids]
        del label_map, label_nodes, label_values
        if np.any(node_communities < 0):
            raise RuntimeError("A selected hypergraph node has no community label")

        original_degrees = np.bincount(edge_nodes, minlength=len(node_ids))
        original_sizes = np.diff(edge_offsets).copy()
        attempts = swaps_per_edge * len(pack_ids)

        # Compile on a tiny example before timing the real shuffle loop.
        probe_offsets = np.array([0, 2, 4], dtype=np.int64)
        probe_nodes = np.array([0, 1, 1, 2], dtype=np.uint32)
        random_edge_shuffles(probe_offsets, probe_nodes, 3, 1, seed)

        random_edge_shuffles(
            edge_offsets,
            edge_nodes,
            len(node_ids),
            attempts,
            seed,
        )
        randomized_degrees = np.bincount(edge_nodes, minlength=len(node_ids))
        if not np.array_equal(original_degrees, randomized_degrees):
            raise RuntimeError("Configuration model changed node degrees")
        if not np.array_equal(original_sizes, np.diff(edge_offsets)):
            raise RuntimeError("Configuration model changed edge sizes")

        entropies = normalized_edge_entropies(
            edge_offsets,
            edge_nodes,
            node_communities,
            community_count,
        )

        import pyarrow as pa

        entropy_table = pa.table(
            {
                "pack_id": pa.array(pack_ids),
                "normalized_entropy": pa.array(entropies),
            }
        )
        connection.register("_configuration_entropy", entropy_table)
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE results.{output_table_name} AS
            SELECT
                pack_id::UINTEGER AS pack_id,
                normalized_entropy::DOUBLE AS normalized_entropy
            FROM _configuration_entropy
            """
        )
        output = (
            settings.parquet_outputs
            / f"{output_table_name}.parquet"
        )
        export_query(
            connection,
            f"""
            SELECT *
            FROM results.{output_table_name}
            ORDER BY pack_id
            """,
            output,
        )

    summary = {
        "independently_recomputed": True,
        "paper_compatible_random_edge_shuffle": True,
        "community_labels_source": labels_source,
        "label_source": label_source,
        "labeled_node_count": label_count,
        "community_count": community_count,
        "pack_count": int(len(pack_ids)),
        "incidence_count": int(len(edge_nodes)),
        "swaps_per_edge": swaps_per_edge,
        "shuffle_attempts": attempts,
        "seed": seed,
        "node_degrees_preserved": True,
        "edge_sizes_preserved": True,
        "mean_normalized_entropy": float(entropies.mean()),
        "zero_entropy_fraction": float(np.count_nonzero(entropies == 0) / len(entropies)),
    }
    result = RunResult(
        task=output_table_name,
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary=summary,
    )
    return _write_result_summary(settings, result)


def sample_large_pack_pairs(
    members: np.ndarray,
    *,
    sample_size: int,
    rng: random.Random,
) -> tuple[list[tuple[int, int]], float]:
    """Match the paper's unique-pair sampling for one large Starter Pack."""
    member_list = [int(member) for member in members]
    member_count = len(member_list)
    total_pairs = member_count * (member_count - 1) // 2
    if total_pairs == 0:
        return [], 0.0

    if total_pairs <= sample_size:
        pairs = [
            (member_list[left], member_list[right])
            for left in range(member_count)
            for right in range(left + 1, member_count)
        ]
    else:
        sampled: set[tuple[int, int]] = set()
        while len(sampled) < sample_size:
            left, right = rng.sample(member_list, 2)
            sampled.add((min(left, right), max(left, right)))
        pairs = sorted(sampled)

    return pairs, total_pairs / len(pairs)


def _pair_work_directory(
    settings: DuckDBSettings,
    *,
    maximum_exact_pack_size: int,
    sample_size: int,
    partitions: int,
    seed: int,
) -> Path:
    return (
        settings.database.parent
        / "pair_cooccurrence"
        / (
            f"max_{maximum_exact_pack_size}_sample_{sample_size}"
            f"_partitions_{partitions}_seed_{seed}"
        )
    )


def _safe_remove_pair_work(path: Path, settings: DuckDBSettings) -> None:
    expected_parent = (settings.database.parent / "pair_cooccurrence").resolve()
    target = path.resolve()
    if target.parent != expected_parent:
        raise RuntimeError(f"Refusing to remove unexpected path: {target}")
    if target.exists():
        shutil.rmtree(target)


def _projection_directory(
    settings: DuckDBSettings,
    *,
    partitions: int,
) -> Path:
    return (
        settings.database.parent
        / "clique_projection"
        / f"partitions_{partitions}"
    )


def _safe_remove_projection(path: Path, settings: DuckDBSettings) -> None:
    expected_parent = (settings.database.parent / "clique_projection").resolve()
    target = path.resolve()
    if target.parent != expected_parent:
        raise RuntimeError(f"Refusing to remove unexpected path: {target}")
    if target.exists():
        shutil.rmtree(target)


def compute_pair_cooccurrence_paper(
    *,
    maximum_exact_pack_size: int = 4_069,
    sample_size: int = 1_000,
    partitions: int = 256,
    seed: int = 0,
    rebuild_pairs: bool = False,
    keep_pair_rows: bool = False,
) -> RunResult:
    """Run the paper-compatible exact/sampled pair co-occurrence pipeline."""
    if maximum_exact_pack_size < 2:
        raise ValueError("maximum_exact_pack_size must be at least 2")
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    if partitions < 16 or partitions > 1024:
        raise ValueError("partitions must be between 16 and 1024")

    settings = load_settings()
    started = time.perf_counter()
    work_directory = _pair_work_directory(
        settings,
        maximum_exact_pack_size=maximum_exact_pack_size,
        sample_size=sample_size,
        partitions=partitions,
        seed=seed,
    )
    marker = work_directory / "_COMPLETE.json"

    with connect(settings) as connection:
        connection.execute(
            """
            CREATE OR REPLACE TABLE meta.starterpack_memberships_unique AS
            SELECT DISTINCT
                pack_id::UINTEGER AS pack_id,
                member_id::UINTEGER AS member_id
            FROM starterpack_memberships
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE meta.starterpack_unique_sizes AS
            SELECT
                pack_id,
                count(*)::UINTEGER AS member_count
            FROM meta.starterpack_memberships_unique
            GROUP BY pack_id
            """
        )

        (
            unique_memberships,
            nonempty_packs,
            maximum_pack_size,
            exact_pair_rows,
            large_pack_count,
            large_pack_total_pairs,
        ) = connection.execute(
            f"""
            SELECT
                sum(member_count)::UBIGINT,
                count(*)::UINTEGER,
                max(member_count)::UINTEGER,
                coalesce(
                    sum(
                        member_count::HUGEINT * (member_count - 1) / 2
                    ) FILTER (
                        WHERE member_count <= {maximum_exact_pack_size}
                    ),
                    0
                )::UBIGINT,
                count(*) FILTER (
                    WHERE member_count > {maximum_exact_pack_size}
                )::UINTEGER,
                coalesce(
                    sum(
                        member_count::HUGEINT * (member_count - 1) / 2
                    ) FILTER (
                        WHERE member_count > {maximum_exact_pack_size}
                    ),
                    0
                )::UBIGINT
            FROM meta.starterpack_unique_sizes
            """
        ).fetchone()

        large_rows = connection.execute(
            f"""
            SELECT m.pack_id, m.member_id
            FROM meta.starterpack_memberships_unique AS m
            JOIN meta.starterpack_unique_sizes AS sizes USING (pack_id)
            WHERE sizes.member_count > {maximum_exact_pack_size}
            ORDER BY m.pack_id, m.member_id
            """
        ).fetchall()

        rng = random.Random(seed)
        sampled_rows: list[tuple[int, int, int, float]] = []
        current_pack: int | None = None
        current_members: list[int] = []

        def finish_large_pack() -> None:
            if current_pack is None:
                return
            pairs, scaling_factor = sample_large_pack_pairs(
                np.asarray(current_members, dtype=np.uint32),
                sample_size=sample_size,
                rng=rng,
            )
            sampled_rows.extend(
                (current_pack, left, right, scaling_factor)
                for left, right in pairs
            )

        for pack_id, member_id in large_rows:
            pack_id = int(pack_id)
            if current_pack is not None and pack_id != current_pack:
                finish_large_pack()
                current_members = []
            current_pack = pack_id
            current_members.append(int(member_id))
        finish_large_pack()

        connection.execute(
            """
            CREATE OR REPLACE TEMP TABLE sampled_large_pack_pairs (
                pack_id UINTEGER,
                node_a UINTEGER,
                node_b UINTEGER,
                weight DOUBLE
            )
            """
        )
        if sampled_rows:
            connection.executemany(
                "INSERT INTO sampled_large_pack_pairs VALUES (?, ?, ?, ?)",
                sampled_rows,
            )

        if rebuild_pairs:
            _safe_remove_pair_work(work_directory, settings)
        elif work_directory.exists() and not marker.exists():
            raise RuntimeError(
                f"Incomplete pair work directory exists: {work_directory}. "
                "Rerun with --rebuild-pairs."
            )

        if not marker.exists():
            work_directory.parent.mkdir(parents=True, exist_ok=True)
            connection.execute(
                f"SET partitioned_write_max_open_files = {partitions}"
            )
            print(
                f"Generating {int(exact_pair_rows):,} exact pair rows "
                f"and {len(sampled_rows):,} sampled rows...",
                flush=True,
            )
            connection.execute(
                f"""
                COPY (
                    WITH all_pair_occurrences AS (
                        SELECT
                            a.member_id AS node_a,
                            b.member_id AS node_b,
                            1.0::DOUBLE AS weight
                        FROM meta.starterpack_memberships_unique AS a
                        JOIN meta.starterpack_memberships_unique AS b
                          ON a.pack_id = b.pack_id
                         AND a.member_id < b.member_id
                        JOIN meta.starterpack_unique_sizes AS sizes
                          ON sizes.pack_id = a.pack_id
                        WHERE sizes.member_count
                              BETWEEN 2 AND {maximum_exact_pack_size}

                        UNION ALL

                        SELECT node_a, node_b, weight
                        FROM sampled_large_pack_pairs
                    )
                    SELECT
                        node_a::UINTEGER AS node_a,
                        node_b::UINTEGER AS node_b,
                        weight,
                        (
                            hash(node_a, node_b) % {partitions}
                        )::USMALLINT AS pair_bucket
                    FROM all_pair_occurrences
                )
                TO {sql_path(work_directory)}
                (
                    FORMAT PARQUET,
                    PARTITION_BY (pair_bucket),
                    COMPRESSION ZSTD,
                    ROW_GROUP_SIZE 122880
                )
                """
            )
            marker.write_text(
                json.dumps(
                    {
                        "maximum_exact_pack_size": maximum_exact_pack_size,
                        "sample_size": sample_size,
                        "partitions": partitions,
                        "seed": seed,
                        "exact_pair_rows": int(exact_pair_rows),
                        "sampled_pair_rows": len(sampled_rows),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        else:
            print(f"Reusing completed pair rows from {work_directory}", flush=True)

        pair_files = list(work_directory.rglob("*.parquet"))
        pair_file_bytes = sum(path.stat().st_size for path in pair_files)
        connection.execute(
            """
            CREATE OR REPLACE TEMP TABLE pair_distribution_parts (
                cooccurrence UBIGINT,
                pair_count UBIGINT
            )
            """
        )

        for bucket in range(partitions):
            bucket_directory = work_directory / f"pair_bucket={bucket}"
            files = list(bucket_directory.glob("*.parquet"))
            if not files:
                continue
            bucket_glob = sql_path(bucket_directory / "*.parquet")
            connection.execute(
                f"""
                INSERT INTO pair_distribution_parts
                WITH pair_totals AS (
                    SELECT
                        node_a,
                        node_b,
                        round_even(sum(weight), 0)::UBIGINT AS cooccurrence
                    FROM read_parquet({bucket_glob})
                    GROUP BY node_a, node_b
                )
                SELECT
                    cooccurrence,
                    count(*)::UBIGINT AS pair_count
                FROM pair_totals
                WHERE cooccurrence > 0
                GROUP BY cooccurrence
                """
            )
            if (bucket + 1) % 32 == 0:
                print(
                    f"Aggregated {bucket + 1}/{partitions} pair buckets",
                    flush=True,
                )

        connection.execute(
            """
            CREATE OR REPLACE TABLE
                results.pair_cooccurrence_paper_compatible AS
            SELECT
                cooccurrence,
                sum(pair_count)::UBIGINT AS pair_count
            FROM pair_distribution_parts
            GROUP BY cooccurrence
            ORDER BY cooccurrence
            """
        )
        output = (
            settings.parquet_outputs
            / "pair_cooccurrence_paper_compatible.parquet"
        )
        export_query(
            connection,
            """
            SELECT *
            FROM results.pair_cooccurrence_paper_compatible
            ORDER BY cooccurrence
            """,
            output,
        )
        distinct_pairs, maximum_cooccurrence = connection.execute(
            """
            SELECT sum(pair_count), max(cooccurrence)
            FROM results.pair_cooccurrence_paper_compatible
            """
        ).fetchone()

    temporary_rows_removed = False
    if not keep_pair_rows:
        _safe_remove_pair_work(work_directory, settings)
        temporary_rows_removed = True

    summary = {
        "paper_compatible": True,
        "exact_for_all_local_packs": large_pack_count == 0,
        "maximum_exact_pack_size": maximum_exact_pack_size,
        "sample_size_for_large_packs": sample_size,
        "seed": seed,
        "partitions": partitions,
        "nonempty_pack_count": nonempty_packs,
        "unique_membership_count": unique_memberships,
        "maximum_unique_pack_size": maximum_pack_size,
        "exact_pair_occurrence_rows": exact_pair_rows,
        "large_pack_count": large_pack_count,
        "large_pack_total_pairs": large_pack_total_pairs,
        "sampled_pair_rows": len(sampled_rows),
        "distinct_pairs": distinct_pairs,
        "maximum_cooccurrence": maximum_cooccurrence,
        "temporary_pair_file_count": len(pair_files),
        "temporary_pair_file_bytes": pair_file_bytes,
        "temporary_pair_rows_removed": temporary_rows_removed,
    }
    result = RunResult(
        task="pair_cooccurrence_paper_compatible",
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary=summary,
    )
    return _write_result_summary(settings, result)


def build_weighted_clique_projection(
    *,
    maximum_exact_pack_size: int = 4_069,
    sample_size: int = 1_000,
    partitions: int = 256,
    seed: int = 0,
    rebuild_projection: bool = False,
) -> RunResult:
    """Materialize the paper-compatible weighted clique projection on disk.

    The large edge relation remains partitioned Parquet. DuckDB stores only a
    view over those files and compact node/distribution statistics.
    """
    if partitions < 16 or partitions > 1024:
        raise ValueError("partitions must be between 16 and 1024")

    settings = load_settings()
    started = time.perf_counter()
    projection_directory = _projection_directory(
        settings,
        partitions=partitions,
    )
    projection_marker = projection_directory / "_COMPLETE.json"
    pair_directory = _pair_work_directory(
        settings,
        maximum_exact_pack_size=maximum_exact_pack_size,
        sample_size=sample_size,
        partitions=partitions,
        seed=seed,
    )

    if rebuild_projection:
        _safe_remove_projection(projection_directory, settings)
    elif projection_directory.exists() and not projection_marker.exists():
        raise RuntimeError(
            f"Incomplete projection directory exists: {projection_directory}. "
            "Rerun with --rebuild-projection."
        )

    if not projection_marker.exists():
        # This creates partitioned pair-occurrence rows. The files are removed
        # after their grouped weighted projection has been safely completed.
        compute_pair_cooccurrence_paper(
            maximum_exact_pack_size=maximum_exact_pack_size,
            sample_size=sample_size,
            partitions=partitions,
            seed=seed,
            keep_pair_rows=True,
        )

        projection_directory.mkdir(parents=True, exist_ok=True)
        with connect(settings) as connection:
            for bucket in range(partitions):
                source_directory = pair_directory / f"pair_bucket={bucket}"
                source_files = list(source_directory.glob("*.parquet"))
                if not source_files:
                    continue
                target_directory = (
                    projection_directory / f"pair_bucket={bucket}"
                )
                target_directory.mkdir(parents=True, exist_ok=True)
                target_file = target_directory / "projection.parquet"
                connection.execute(
                    f"""
                    COPY (
                        SELECT
                            node_a::UINTEGER AS node_a,
                            node_b::UINTEGER AS node_b,
                            round_even(sum(weight), 0)::UBIGINT
                                AS cooccurrence
                        FROM read_parquet(
                            {sql_path(source_directory / "*.parquet")}
                        )
                        GROUP BY node_a, node_b
                        HAVING round_even(sum(weight), 0) > 0
                    )
                    TO {sql_path(target_file)}
                    (
                        FORMAT PARQUET,
                        COMPRESSION ZSTD,
                        ROW_GROUP_SIZE 122880
                    )
                    """
                )
                if (bucket + 1) % 32 == 0:
                    print(
                        f"Materialized {bucket + 1}/{partitions} "
                        "projection buckets",
                        flush=True,
                    )

        projection_files = list(projection_directory.rglob("*.parquet"))
        if not projection_files:
            raise RuntimeError("Projection build produced no Parquet files")
        projection_marker.write_text(
            json.dumps(
                {
                    "maximum_exact_pack_size": maximum_exact_pack_size,
                    "sample_size": sample_size,
                    "partitions": partitions,
                    "seed": seed,
                    "file_count": len(projection_files),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        _safe_remove_pair_work(pair_directory, settings)
    else:
        print(
            f"Reusing completed projection from {projection_directory}",
            flush=True,
        )

    projection_files = list(projection_directory.rglob("*.parquet"))
    projection_file_bytes = sum(path.stat().st_size for path in projection_files)
    projection_glob = sql_path(projection_directory / "*" / "*.parquet")
    with connect(settings) as connection:
        connection.execute(
            f"""
            CREATE OR REPLACE VIEW starterpack_clique_projection AS
            SELECT
                node_a::UINTEGER AS node_a,
                node_b::UINTEGER AS node_b,
                cooccurrence::UBIGINT AS cooccurrence,
                pair_bucket::USMALLINT AS pair_bucket
            FROM read_parquet({projection_glob}, hive_partitioning = true)
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE
                results.starterpack_projection_node_stats_local AS
            WITH endpoint_parts AS (
                SELECT
                    node_a AS node_id,
                    count(*)::UBIGINT AS degree,
                    sum(cooccurrence)::UBIGINT AS strength
                FROM starterpack_clique_projection
                GROUP BY node_a

                UNION ALL

                SELECT
                    node_b AS node_id,
                    count(*)::UBIGINT AS degree,
                    sum(cooccurrence)::UBIGINT AS strength
                FROM starterpack_clique_projection
                GROUP BY node_b
            )
            SELECT
                node_id::UINTEGER AS node_id,
                sum(degree)::UBIGINT AS degree,
                sum(strength)::UBIGINT AS strength
            FROM endpoint_parts
            GROUP BY node_id
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE
                results.starterpack_projection_degree_distribution_local AS
            SELECT
                degree,
                count(*)::UBIGINT AS node_count
            FROM results.starterpack_projection_node_stats_local
            GROUP BY degree
            ORDER BY degree
            """
        )

        node_output = (
            settings.parquet_outputs
            / "starterpack_projection_node_stats_local.parquet"
        )
        degree_output = (
            settings.parquet_outputs
            / "starterpack_projection_degree_distribution_local.parquet"
        )
        export_query(
            connection,
            """
            SELECT *
            FROM results.starterpack_projection_node_stats_local
            ORDER BY node_id
            """,
            node_output,
        )
        export_query(
            connection,
            """
            SELECT *
            FROM results.starterpack_projection_degree_distribution_local
            ORDER BY degree
            """,
            degree_output,
        )
        (
            edge_count,
            projected_node_count,
            maximum_degree,
            maximum_strength,
            maximum_cooccurrence,
        ) = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM starterpack_clique_projection),
                count(*),
                max(degree),
                max(strength),
                (
                    SELECT max(cooccurrence)
                    FROM starterpack_clique_projection
                )
            FROM results.starterpack_projection_node_stats_local
            """
        ).fetchone()
        expected_edge_count = connection.execute(
            """
            SELECT sum(pair_count)
            FROM results.pair_cooccurrence_paper_compatible
            """
        ).fetchone()[0]
        if edge_count != expected_edge_count:
            raise RuntimeError(
                "Projection edge count does not match pair distribution: "
                f"{edge_count} != {expected_edge_count}"
            )

    pair_summary_path = (
        settings.summary_outputs / "pair_cooccurrence_paper_compatible.json"
    )
    if pair_summary_path.exists():
        pair_payload = json.loads(pair_summary_path.read_text(encoding="utf-8"))
        pair_payload["summary"]["temporary_pair_rows_removed"] = True
        pair_summary_path.write_text(
            json.dumps(pair_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    summary = {
        "paper_compatible": True,
        "disk_backed": True,
        "projection_directory": str(projection_directory),
        "partitions": partitions,
        "projection_file_count": len(projection_files),
        "projection_file_bytes": projection_file_bytes,
        "edge_count": edge_count,
        "projected_node_count": projected_node_count,
        "maximum_degree": maximum_degree,
        "maximum_strength": maximum_strength,
        "maximum_cooccurrence": maximum_cooccurrence,
        "temporary_pair_rows_removed": True,
    }
    result = RunResult(
        task="starterpack_weighted_clique_projection",
        seconds=time.perf_counter() - started,
        outputs=[
            str(projection_directory),
            str(node_output),
            str(degree_output),
        ],
        summary=summary,
    )
    return _write_result_summary(settings, result)
