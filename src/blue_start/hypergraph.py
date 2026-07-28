from __future__ import annotations

import collections
import gzip
import heapq
import json
import math
import time
from pathlib import Path
from typing import Any

from .duckdb_backend import connect, export_query
from .paths import resolved_datasets
from .pipeline import RunResult
from .settings import load_settings


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.size: dict[int, int] = {}

    def add(self, node: int) -> None:
        if node not in self.parent:
            self.parent[node] = node
            self.size[node] = 1

    def find(self, node: int) -> int:
        parent = self.parent[node]
        while parent != self.parent[parent]:
            parent = self.parent[parent]
        while node != parent:
            next_node = self.parent[node]
            self.parent[node] = parent
            node = next_node
        return parent

    def union(self, left: int, right: int) -> None:
        self.add(left)
        self.add(right)
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.size[root_left] < self.size[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        self.size[root_left] += self.size.pop(root_right)


def compute_starterpack_components() -> RunResult:
    """Compute exact hypergraph components without constructing a clique graph."""
    settings = load_settings()
    started = time.perf_counter()
    uf = UnionFind()

    with connect(settings) as con:
        cursor = con.execute(
            """
            SELECT pack_id, member_id
            FROM starterpack_memberships
            ORDER BY pack_id
            """
        )
        current_pack: int | None = None
        anchor: int | None = None
        while True:
            rows = cursor.fetchmany(250_000)
            if not rows:
                break
            for pack_id, member_id in rows:
                pack_id = int(pack_id)
                member_id = int(member_id)
                uf.add(member_id)
                if pack_id != current_pack:
                    current_pack = pack_id
                    anchor = member_id
                elif anchor is not None:
                    uf.union(anchor, member_id)

        component_sizes = collections.Counter(
            uf.size[uf.find(node)] for node in uf.parent if uf.find(node) == node
        )
        # The expression above counts roots by their sizes. Build the distribution
        # explicitly to keep the output tiny.
        size_distribution = sorted(
            (component_size, component_count)
            for component_size, component_count in component_sizes.items()
        )

        con.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_component_sizes (
                component_size UINTEGER,
                component_count UINTEGER
            )
            """
        )
        if size_distribution:
            con.executemany(
                "INSERT INTO results.starterpack_component_sizes VALUES (?, ?)",
                size_distribution,
            )
        output = settings.parquet_outputs / "starterpack_component_sizes.parquet"
        export_query(
            con,
            "SELECT * FROM results.starterpack_component_sizes ORDER BY component_size",
            output,
        )

    total_components = sum(component_sizes.values())
    largest = max(component_sizes) if component_sizes else 0
    summary = {
        "component_count": total_components,
        "largest_component_size": largest,
        "node_count": len(uf.parent),
    }
    result = RunResult(
        task="starterpack_components",
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary=summary,
    )
    summary_path = settings.summary_outputs / "starterpack_components.json"
    summary_path.write_text(
        json.dumps(
            {
                "task": result.task,
                "seconds": result.seconds,
                "outputs": result.outputs,
                "summary": summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return RunResult(
        result.task,
        result.seconds,
        result.outputs + [str(summary_path)],
        result.summary,
    )


def compute_pair_cooccurrence(*, max_pack_size: int = 50) -> RunResult:
    """
    Compute exact user-pair co-occurrences for packs up to max_pack_size.

    The full unfiltered calculation can create billions of intermediate pairs.
    DuckDB can spill, but the caller must explicitly raise max_pack_size.
    """
    if max_pack_size < 2:
        raise ValueError("max_pack_size must be at least 2")
    settings = load_settings()
    started = time.perf_counter()
    label = f"max_pack_{max_pack_size}"

    with connect(settings) as con:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE results.pair_cooccurrence_{label} AS
            WITH pair_counts AS (
                SELECT
                    a.member_id AS node_a,
                    b.member_id AS node_b,
                    count(*)::UINTEGER AS cooccurrence
                FROM starterpack_memberships a
                JOIN starterpack_memberships b
                  ON a.pack_id = b.pack_id
                 AND a.member_id < b.member_id
                JOIN starterpacks p ON p.pack_id = a.pack_id
                WHERE p.member_count BETWEEN 2 AND {int(max_pack_size)}
                GROUP BY a.member_id, b.member_id
            )
            SELECT
                cooccurrence,
                count(*)::UBIGINT AS pair_count
            FROM pair_counts
            GROUP BY cooccurrence
            ORDER BY cooccurrence
            """
        )
        output = (
            settings.parquet_outputs / f"pair_cooccurrence_{label}.parquet"
        )
        export_query(
            con,
            f"SELECT * FROM results.pair_cooccurrence_{label}",
            output,
        )
        total_pairs, max_cooccurrence = con.execute(
            f"""
            SELECT sum(pair_count), max(cooccurrence)
            FROM results.pair_cooccurrence_{label}
            """
        ).fetchone()

    summary = {
        "max_pack_size": max_pack_size,
        "distinct_pairs": total_pairs,
        "maximum_cooccurrence": max_cooccurrence,
        "exact_for_included_packs": True,
    }
    result = RunResult(
        task=f"pair_cooccurrence_{label}",
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary=summary,
    )
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
        ),
        encoding="utf-8",
    )
    return RunResult(
        result.task,
        result.seconds,
        result.outputs + [str(summary_path)],
        result.summary,
    )


def compute_s_line_counts(
    *,
    s_max: int = 5,
    max_member_degree: int = 5_000,
) -> RunResult:
    """
    Compute exact s-line counts after filtering hyper-hubs.

    An unfiltered run is not practical on a 32 GB workstation because one member
    appearing in ~175k packs alone induces more than 15 billion candidate pairs.
    """
    if s_max < 1:
        raise ValueError("s_max must be positive")
    if max_member_degree < 2:
        raise ValueError("max_member_degree must be at least 2")
    settings = load_settings()
    started = time.perf_counter()
    label = f"s{s_max}_member_degree_{max_member_degree}"

    with connect(settings) as con:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE results.s_line_{label} AS
            WITH member_degree AS (
                SELECT member_id, count(*) AS degree
                FROM starterpack_memberships
                GROUP BY member_id
                HAVING count(*) <= {int(max_member_degree)}
            ),
            pack_overlaps AS (
                SELECT
                    a.pack_id AS pack_a,
                    b.pack_id AS pack_b,
                    count(*)::UINTEGER AS overlap
                FROM starterpack_memberships a
                JOIN member_degree d USING (member_id)
                JOIN starterpack_memberships b
                  ON a.member_id = b.member_id
                 AND a.pack_id < b.pack_id
                GROUP BY a.pack_id, b.pack_id
            ),
            thresholds AS (
                SELECT range::UINTEGER AS s
                FROM range(1, {int(s_max) + 1})
            ),
            edge_counts AS (
                SELECT
                    t.s,
                    count(*) FILTER (WHERE o.overlap >= t.s)::UBIGINT AS edges
                FROM thresholds t
                CROSS JOIN pack_overlaps o
                GROUP BY t.s
            ),
            active_packs AS (
                SELECT t.s, o.pack_a AS pack_id
                FROM thresholds t
                JOIN pack_overlaps o ON o.overlap >= t.s
                UNION
                SELECT t.s, o.pack_b AS pack_id
                FROM thresholds t
                JOIN pack_overlaps o ON o.overlap >= t.s
            )
            SELECT
                e.s,
                e.edges,
                count(a.pack_id)::UINTEGER AS nodes
            FROM edge_counts e
            LEFT JOIN active_packs a USING (s)
            GROUP BY e.s, e.edges
            ORDER BY e.s
            """
        )
        output = settings.parquet_outputs / f"s_line_{label}.parquet"
        export_query(con, f"SELECT * FROM results.s_line_{label}", output)
        rows = con.execute(
            f"SELECT s, edges FROM results.s_line_{label} ORDER BY s"
        ).fetchall()

    summary = {
        "s_max": s_max,
        "max_member_degree": max_member_degree,
        "edge_counts": {str(s): edges for s, edges in rows},
        "note": "Exact only for the hypergraph after filtering high-degree members.",
    }
    result = RunResult(
        task=f"s_line_{label}",
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary=summary,
    )
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
        ),
        encoding="utf-8",
    )
    return RunResult(
        result.task,
        result.seconds,
        result.outputs + [str(summary_path)],
        result.summary,
    )


def compute_hypergraph_kcore() -> RunResult:
    """
    Port of the upstream hypergraph k-core algorithm using the JSONL packs.

    This is exact and avoids XGI's duplicate object model, but it is still a
    multi-gigabyte Python workload. It is intentionally a separate command.
    """
    path = resolved_datasets()["starterpacks_jsonl"]
    if path is None:
        raise FileNotFoundError("starterpacks_jsonl is missing")
    settings = load_settings()
    started = time.perf_counter()

    edge_to_nodes: list[set[int]] = []
    node_to_edges: dict[int, set[int]] = collections.defaultdict(set)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for edge_index, line in enumerate(handle):
            record = json.loads(line)
            nodes = {int(member["id"]) for member in record["members"]}
            edge_to_nodes.append(nodes)
            for node in nodes:
                node_to_edges[node].add(edge_index)

    degree = {node: len(edges) for node, edges in node_to_edges.items()}
    heap = [(value, node) for node, value in degree.items()]
    heapq.heapify(heap)
    removed: set[int] = set()
    core: dict[int, int] = {}

    while heap:
        candidate_degree, node = heapq.heappop(heap)
        if node in removed or candidate_degree != degree[node]:
            continue
        removed.add(node)
        core[node] = candidate_degree
        for edge_index in list(node_to_edges[node]):
            edge_nodes = edge_to_nodes[edge_index]
            edge_nodes.discard(node)
            if len(edge_nodes) < 2:
                for remaining in list(edge_nodes):
                    if remaining in removed:
                        continue
                    if edge_index in node_to_edges[remaining]:
                        node_to_edges[remaining].remove(edge_index)
                        degree[remaining] -= 1
                        heapq.heappush(heap, (degree[remaining], remaining))
                edge_nodes.clear()
        del node_to_edges[node]

    distribution = sorted(collections.Counter(core.values()).items())
    with connect(settings) as con:
        con.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_kcore_distribution (
                core_number UINTEGER,
                node_count UBIGINT
            )
            """
        )
        con.executemany(
            "INSERT INTO results.starterpack_kcore_distribution VALUES (?, ?)",
            distribution,
        )
        output = settings.parquet_outputs / "starterpack_kcore_distribution.parquet"
        export_query(
            con,
            "SELECT * FROM results.starterpack_kcore_distribution ORDER BY core_number",
            output,
        )

    summary = {
        "node_count": len(core),
        "maximum_core": max(core.values(), default=0),
        "nodes_with_core_at_least_1000": sum(
            count for value, count in distribution if value >= 1000
        ),
    }
    result = RunResult(
        task="starterpack_kcore",
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary=summary,
    )
    summary_path = settings.summary_outputs / "starterpack_kcore.json"
    summary_path.write_text(
        json.dumps(
            {
                "task": result.task,
                "seconds": result.seconds,
                "outputs": result.outputs,
                "summary": result.summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return RunResult(
        result.task,
        result.seconds,
        result.outputs + [str(summary_path)],
        result.summary,
    )
