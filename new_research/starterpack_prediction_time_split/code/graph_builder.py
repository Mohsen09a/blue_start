"""Build pre-cutoff follow features and leakage-free recommendation candidates."""

from __future__ import annotations

import time
from typing import Any

import duckdb
import pyarrow as pa

from .data_loader import StudyConfig, run_sql_stage


def build_history_graph_features(
    connection: duckdb.DuckDBPyConnection,
    config: StudyConfig,
    *,
    force: bool,
) -> None:
    """Aggregate graph information using edges no later than history_end."""
    run_sql_stage(
        connection,
        "history_follow_out_degree",
        "meta.history_follow_out_degree",
        f"""
        CREATE OR REPLACE TABLE meta.history_follow_out_degree AS
        SELECT n.node_id, count(*)::UBIGINT AS out_degree
        FROM source.main.follows AS f
        JOIN meta.history_nodes AS n ON n.node_id=f.src
        WHERE f.date_followed BETWEEN DATE '2022-11-17' AND DATE '{config.history_end}'
        GROUP BY n.node_id
        """,
        force=force,
        details={"edge_cutoff": config.history_end},
    )
    run_sql_stage(
        connection,
        "history_follow_in_degree",
        "meta.history_follow_in_degree",
        f"""
        CREATE OR REPLACE TABLE meta.history_follow_in_degree AS
        SELECT n.node_id, count(*)::UBIGINT AS in_degree
        FROM source.main.follows AS f
        JOIN meta.history_nodes AS n ON n.node_id=f.dst
        WHERE f.date_followed BETWEEN DATE '2022-11-17' AND DATE '{config.history_end}'
        GROUP BY n.node_id
        """,
        force=force,
        details={"edge_cutoff": config.history_end},
    )
    run_sql_stage(
        connection,
        "history_node_features",
        "meta.history_node_features",
        """
        CREATE OR REPLACE TABLE meta.history_node_features AS
        SELECT
            n.node_id,
            n.node_index,
            n.history_pack_count,
            coalesce(o.out_degree, 0)::UBIGINT AS history_out_degree,
            coalesce(i.in_degree, 0)::UBIGINT AS history_in_degree
        FROM meta.history_nodes AS n
        LEFT JOIN meta.history_follow_out_degree AS o USING (node_id)
        LEFT JOIN meta.history_follow_in_degree AS i USING (node_id)
        """,
        force=force,
    )
    run_sql_stage(
        connection,
        "creator_follow_candidates",
        "meta.creator_follow_candidates",
        f"""
        CREATE OR REPLACE TABLE meta.creator_follow_candidates AS
        WITH directional AS (
            SELECT
                c.creator_id,
                f.dst::UINTEGER AS candidate_id,
                true AS creator_follows_candidate,
                false AS candidate_follows_creator
            FROM meta.target_creators AS c
            JOIN source.main.follows AS f ON f.src=c.creator_id
            JOIN meta.history_nodes AS n ON n.node_id=f.dst
            WHERE f.date_followed BETWEEN DATE '2022-11-17' AND DATE '{config.history_end}'
              AND f.dst <> c.creator_id
            UNION ALL
            SELECT
                c.creator_id,
                f.src::UINTEGER AS candidate_id,
                false AS creator_follows_candidate,
                true AS candidate_follows_creator
            FROM meta.target_creators AS c
            JOIN source.main.follows AS f ON f.dst=c.creator_id
            JOIN meta.history_nodes AS n ON n.node_id=f.src
            WHERE f.date_followed BETWEEN DATE '2022-11-17' AND DATE '{config.history_end}'
              AND f.src <> c.creator_id
        ), aggregated AS (
            SELECT
                creator_id,
                candidate_id,
                bool_or(creator_follows_candidate) AS creator_follows_candidate,
                bool_or(candidate_follows_creator) AS candidate_follows_creator
            FROM directional
            GROUP BY creator_id, candidate_id
        ), ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY creator_id
                ORDER BY
                    (creator_follows_candidate::INTEGER + candidate_follows_creator::INTEGER) DESC,
                    hash(creator_id, candidate_id, {config.seed})
            ) AS candidate_rank
            FROM aggregated
        )
        SELECT
            creator_id,
            candidate_id,
            creator_follows_candidate,
            candidate_follows_creator
        FROM ranked
        WHERE candidate_rank <= {config.follow_per_creator}
        """,
        force=force,
        details={
            "maximum_per_creator": config.follow_per_creator,
            "edge_cutoff": config.history_end,
        },
    )
    run_sql_stage(
        connection,
        "creator_comember_candidates",
        "meta.creator_comember_candidates",
        f"""
        CREATE OR REPLACE TABLE meta.creator_comember_candidates AS
        WITH counts AS (
            SELECT
                c.creator_id,
                b.member_id::UINTEGER AS candidate_id,
                count(*)::UINTEGER AS shared_history_packs
            FROM meta.target_creators AS c
            JOIN meta.history_memberships AS a ON a.member_id=c.creator_id
            JOIN meta.history_memberships AS b
              ON b.pack_id=a.pack_id AND b.member_id <> c.creator_id
            GROUP BY c.creator_id, b.member_id
        ), ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY creator_id
                ORDER BY shared_history_packs DESC, hash(creator_id, candidate_id, {config.seed})
            ) AS candidate_rank
            FROM counts
        )
        SELECT creator_id, candidate_id, shared_history_packs
        FROM ranked
        WHERE candidate_rank <= {config.comember_per_creator}
        """,
        force=force,
        details={"maximum_per_creator": config.comember_per_creator},
    )
    run_sql_stage(
        connection,
        "global_popular_nodes",
        "meta.global_popular_nodes",
        f"""
        CREATE OR REPLACE TABLE meta.global_popular_nodes AS
        SELECT
            node_id,
            node_index,
            history_pack_count,
            history_out_degree,
            history_in_degree,
            row_number() OVER (
                ORDER BY history_pack_count DESC,
                         history_in_degree DESC,
                         hash(node_id, {config.seed})
            )::UINTEGER AS popularity_rank
        FROM meta.history_node_features
        ORDER BY popularity_rank
        LIMIT {max(1024, config.global_popular_per_creator)}
        """,
        force=force,
    )


def install_embedding_clusters(
    connection: duckdb.DuckDBPyConnection,
    cluster_rows: list[dict[str, Any]] | pa.Table,
    *,
    force: bool,
) -> None:
    if not force:
        exists = connection.execute(
            """
            SELECT count(*) > 0 FROM information_schema.tables
            WHERE table_schema='meta' AND table_name='node_embedding_clusters'
            """
        ).fetchone()[0]
        if exists:
            print("[REUSE] node_embedding_clusters")
            return
    table = cluster_rows if isinstance(cluster_rows, pa.Table) else pa.Table.from_pylist(cluster_rows)
    connection.register("_embedding_clusters", table)
    connection.execute(
        """
        CREATE OR REPLACE TABLE meta.node_embedding_clusters AS
        SELECT
            node_id::UINTEGER AS node_id,
            node_index::UINTEGER AS node_index,
            cluster::USMALLINT AS cluster
        FROM _embedding_clusters
        """
    )
    connection.unregister("_embedding_clusters")


def build_candidates(
    connection: duckdb.DuckDBPyConnection,
    config: StudyConfig,
    *,
    force: bool,
) -> None:
    """Build target-independent candidates and attach labels only afterward."""
    run_sql_stage(
        connection,
        "cluster_popular_nodes",
        "meta.cluster_popular_nodes",
        f"""
        CREATE OR REPLACE TABLE meta.cluster_popular_nodes AS
        WITH ranked AS (
            SELECT
                c.cluster,
                f.node_id,
                f.node_index,
                f.history_pack_count,
                f.history_out_degree,
                f.history_in_degree,
                row_number() OVER (
                    PARTITION BY c.cluster
                    ORDER BY f.history_pack_count DESC,
                             f.history_in_degree DESC,
                             hash(f.node_id, {config.seed})
                ) AS cluster_rank
            FROM meta.node_embedding_clusters AS c
            JOIN meta.history_node_features AS f USING (node_id, node_index)
        )
        SELECT * EXCLUDE (cluster_rank)
        FROM ranked
        WHERE cluster_rank <= {config.cluster_popular_per_creator}
        """,
        force=force,
    )
    run_sql_stage(
        connection,
        "creator_candidate_pool",
        "meta.creator_candidate_pool",
        f"""
        CREATE OR REPLACE TABLE meta.creator_candidate_pool AS
        WITH raw AS (
            SELECT
                creator_id,
                candidate_id,
                creator_follows_candidate,
                candidate_follows_creator,
                0::UINTEGER AS shared_history_packs,
                true AS from_follow,
                false AS from_comember,
                false AS from_cluster,
                false AS from_global
            FROM meta.creator_follow_candidates
            UNION ALL
            SELECT
                creator_id,
                candidate_id,
                false,
                false,
                shared_history_packs,
                false,
                true,
                false,
                false
            FROM meta.creator_comember_candidates
            UNION ALL
            SELECT
                c.creator_id,
                p.node_id AS candidate_id,
                false,
                false,
                0,
                false,
                false,
                true,
                false
            FROM meta.target_creators AS c
            JOIN meta.node_embedding_clusters AS creator_cluster
              ON creator_cluster.node_id=c.creator_id
            JOIN meta.cluster_popular_nodes AS p USING (cluster)
            WHERE p.node_id <> c.creator_id
            UNION ALL
            SELECT
                c.creator_id,
                p.node_id AS candidate_id,
                false,
                false,
                0,
                false,
                false,
                false,
                true
            FROM meta.target_creators AS c
            CROSS JOIN (
                SELECT * FROM meta.global_popular_nodes
                WHERE popularity_rank <= {config.global_popular_per_creator}
            ) AS p
            WHERE p.node_id <> c.creator_id
        ), combined AS (
            SELECT
                creator_id,
                candidate_id,
                bool_or(creator_follows_candidate) AS creator_follows_candidate,
                bool_or(candidate_follows_creator) AS candidate_follows_creator,
                max(shared_history_packs)::UINTEGER AS shared_history_packs,
                bool_or(from_follow) AS from_follow,
                bool_or(from_comember) AS from_comember,
                bool_or(from_cluster) AS from_cluster,
                bool_or(from_global) AS from_global
            FROM raw
            GROUP BY creator_id, candidate_id
        )
        SELECT
            c.*,
            n.node_index AS candidate_index,
            n.history_pack_count AS candidate_pack_count,
            n.history_out_degree AS candidate_out_degree,
            n.history_in_degree AS candidate_in_degree
        FROM combined AS c
        JOIN meta.history_node_features AS n ON n.node_id=c.candidate_id
        """,
        force=force,
        details={
            "sources": ["follow", "historical_comember", "embedding_cluster", "global_popular"]
        },
    )
    run_sql_stage(
        connection,
        "prediction_candidates",
        "results.prediction_candidates",
        f"""
        CREATE OR REPLACE TABLE results.prediction_candidates AS
        WITH expanded AS (
            SELECT
                p.pack_id,
                p.split,
                p.date_created,
                p.creator_id,
                creator_node.node_index AS creator_index,
                c.* EXCLUDE (creator_id),
                coalesce(creator_node.history_pack_count, 0)::UINTEGER AS creator_pack_count,
                coalesce(creator_node.history_out_degree, 0)::UBIGINT AS creator_out_degree,
                coalesce(creator_node.history_in_degree, 0)::UBIGINT AS creator_in_degree,
                (
                    4 * c.creator_follows_candidate::INTEGER
                    + 4 * c.candidate_follows_creator::INTEGER
                    + 3 * least(c.shared_history_packs, 10)
                    + 2 * c.from_cluster::INTEGER
                    + ln(1 + c.candidate_pack_count)
                )::DOUBLE AS retrieval_score
            FROM meta.target_pack_totals AS p
            JOIN meta.creator_candidate_pool AS c USING (creator_id)
            LEFT JOIN meta.history_node_features AS creator_node
              ON creator_node.node_id=p.creator_id
        ), limited AS (
            SELECT *, row_number() OVER (
                PARTITION BY pack_id
                ORDER BY retrieval_score DESC,
                         hash(pack_id, candidate_id, {config.seed})
            ) AS candidate_rank
            FROM expanded
        )
        SELECT
            l.* EXCLUDE (candidate_rank),
            m.member_id IS NOT NULL AS label
        FROM limited AS l
        LEFT JOIN meta.target_memberships AS m
          ON m.pack_id=l.pack_id AND m.member_id=l.candidate_id
        WHERE candidate_rank <= {config.maximum_per_pack}
        """,
        force=force,
        details={
            "maximum_per_pack": config.maximum_per_pack,
            "labels_added_after_target_independent_retrieval": True,
        },
    )


def candidate_summary(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            split,
            count(DISTINCT pack_id)::UBIGINT AS packs,
            count(*)::UBIGINT AS candidates,
            count(*)::DOUBLE / count(DISTINCT pack_id) AS mean_candidates,
            sum(label::INTEGER)::UBIGINT AS retrieved_positives
        FROM results.prediction_candidates
        GROUP BY split
        ORDER BY CASE split WHEN 'train' THEN 1 WHEN 'validation' THEN 2 ELSE 3 END
        """
    ).fetchall()
    # positives_repeated is not useful because the pack total repeats per candidate.
    # Recompute the denominator once per pack in a separate exact query.
    totals = {
        row[0]: int(row[1])
        for row in connection.execute(
            "SELECT split, sum(positive_members) FROM meta.target_pack_totals GROUP BY split"
        ).fetchall()
    }
    result = []
    for row in rows:
        split = row[0]
        result.append(
            {
                "split": split,
                "packs": int(row[1]),
                "candidates": int(row[2]),
                "mean_candidates": float(row[3]),
                "retrieved_positives": int(row[4]),
                "total_positives": totals[split],
                "candidate_recall": int(row[4]) / max(totals[split], 1),
            }
        )
    return result
