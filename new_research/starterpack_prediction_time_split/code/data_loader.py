"""DuckDB data extraction and strict time-split construction."""

from __future__ import annotations

import json
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


STUDY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = STUDY_ROOT.parents[1]
CONFIG_PATH = STUDY_ROOT / "config.toml"
DATABASE_PATH = STUDY_ROOT / "work" / "starterpack_prediction.duckdb"
SOURCE_DATABASE = PROJECT_ROOT / "work" / "blue_start.duckdb"
OUTPUTS = STUDY_ROOT / "outputs"
PARQUET = OUTPUTS / "parquet"
MODELS = OUTPUTS / "models"
SUMMARIES = OUTPUTS / "summaries"
FIGURES = OUTPUTS / "figures"


@dataclass(frozen=True)
class StudyConfig:
    minimum_date: str
    history_end: str
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    test_start: str
    test_end: str
    seed: int
    memory_limit: str
    threads: int
    max_temp_directory_size: str
    dimensions: int
    svd_iterations: int
    clusters: int
    cluster_batch_rows: int
    follow_per_creator: int
    comember_per_creator: int
    cluster_popular_per_creator: int
    global_popular_per_creator: int
    maximum_per_pack: int
    training_negatives_per_pack: int
    model_batch_rows: int
    model_epochs: int
    model_alpha: float
    evaluation_ks: tuple[int, ...]


def load_config() -> StudyConfig:
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    study = raw["study"]
    database = raw["duckdb"]
    embedding = raw["embedding"]
    candidates = raw["candidates"]
    model = raw["model"]
    evaluation = raw["evaluation"]
    return StudyConfig(
        minimum_date=str(study["minimum_date"]),
        history_end=str(study["history_end"]),
        train_start=str(study["train_start"]),
        train_end=str(study["train_end"]),
        validation_start=str(study["validation_start"]),
        validation_end=str(study["validation_end"]),
        test_start=str(study["test_start"]),
        test_end=str(study["test_end"]),
        seed=int(study["seed"]),
        memory_limit=str(database["memory_limit"]),
        threads=int(database["threads"]),
        max_temp_directory_size=str(database["max_temp_directory_size"]),
        dimensions=int(embedding["dimensions"]),
        svd_iterations=int(embedding["svd_iterations"]),
        clusters=int(embedding["clusters"]),
        cluster_batch_rows=int(embedding["cluster_batch_rows"]),
        follow_per_creator=int(candidates["follow_per_creator"]),
        comember_per_creator=int(candidates["comember_per_creator"]),
        cluster_popular_per_creator=int(candidates["cluster_popular_per_creator"]),
        global_popular_per_creator=int(candidates["global_popular_per_creator"]),
        maximum_per_pack=int(candidates["maximum_per_pack"]),
        training_negatives_per_pack=int(candidates["training_negatives_per_pack"]),
        model_batch_rows=int(model["batch_rows"]),
        model_epochs=int(model["epochs"]),
        model_alpha=float(model["alpha"]),
        evaluation_ks=tuple(int(value) for value in evaluation["ks"]),
    )


def ensure_directories() -> None:
    for path in (
        STUDY_ROOT / "work",
        STUDY_ROOT / "work" / "duckdb_tmp",
        STUDY_ROOT / "work" / "arrays",
        OUTPUTS,
        PARQUET,
        MODELS,
        SUMMARIES,
        FIGURES,
    ):
        path.mkdir(parents=True, exist_ok=True)


def connect(config: StudyConfig) -> duckdb.DuckDBPyConnection:
    ensure_directories()
    connection = duckdb.connect(str(DATABASE_PATH))
    connection.execute(f"SET memory_limit='{config.memory_limit}'")
    connection.execute(f"SET threads={config.threads}")
    connection.execute("SET preserve_insertion_order=false")
    connection.execute(
        f"SET temp_directory='{(STUDY_ROOT / 'work' / 'duckdb_tmp').as_posix()}'"
    )
    connection.execute(
        f"SET max_temp_directory_size='{config.max_temp_directory_size}'"
    )
    connection.execute("CREATE SCHEMA IF NOT EXISTS meta")
    connection.execute("CREATE SCHEMA IF NOT EXISTS results")
    attached = {
        row[1] for row in connection.execute("PRAGMA database_list").fetchall()
    }
    if "source" not in attached:
        connection.execute(
            f"ATTACH '{SOURCE_DATABASE.as_posix()}' AS source (READ_ONLY)"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS meta.stages (
            stage VARCHAR PRIMARY KEY,
            completed_at TIMESTAMP,
            elapsed_seconds DOUBLE,
            row_count UBIGINT,
            details VARCHAR
        )
        """
    )
    return connection


def table_exists(connection: duckdb.DuckDBPyConnection, table: str) -> bool:
    schema, name = table.split(".", maxsplit=1)
    return bool(
        connection.execute(
            """
            SELECT count(*) > 0
            FROM information_schema.tables
            WHERE table_schema=? AND table_name=?
            """,
            [schema, name],
        ).fetchone()[0]
    )


def run_sql_stage(
    connection: duckdb.DuckDBPyConnection,
    stage: str,
    target_table: str,
    sql: str,
    *,
    force: bool,
    details: dict[str, Any] | None = None,
) -> int:
    if table_exists(connection, target_table) and not force:
        rows = int(connection.execute(f"SELECT count(*) FROM {target_table}").fetchone()[0])
        print(f"[REUSE] {stage}: {rows:,} rows")
        return rows
    print(f"[BUILD] {stage}")
    started = time.perf_counter()
    connection.execute(sql)
    rows = int(connection.execute(f"SELECT count(*) FROM {target_table}").fetchone()[0])
    elapsed = time.perf_counter() - started
    connection.execute(
        """
        INSERT OR REPLACE INTO meta.stages
        VALUES (?, current_timestamp, ?, ?, ?)
        """,
        [stage, elapsed, rows, json.dumps(details or {}, sort_keys=True)],
    )
    print(f"[OK] {stage}: {rows:,} rows in {elapsed:.2f}s")
    return rows


def build_temporal_tables(
    connection: duckdb.DuckDBPyConnection,
    config: StudyConfig,
    *,
    force: bool,
) -> None:
    """Create leakage-safe history, target, and label tables."""
    run_sql_stage(
        connection,
        "pack_splits",
        "meta.pack_splits",
        f"""
        CREATE OR REPLACE TABLE meta.pack_splits AS
        SELECT
            pack_id::UINTEGER AS pack_id,
            creator_id::UINTEGER AS creator_id,
            date_created::DATE AS date_created,
            CASE
                WHEN date_created BETWEEN DATE '{config.minimum_date}' AND DATE '{config.history_end}'
                    THEN 'history'
                WHEN date_created BETWEEN DATE '{config.train_start}' AND DATE '{config.train_end}'
                    THEN 'train'
                WHEN date_created BETWEEN DATE '{config.validation_start}' AND DATE '{config.validation_end}'
                    THEN 'validation'
                WHEN date_created BETWEEN DATE '{config.test_start}' AND DATE '{config.test_end}'
                    THEN 'test'
                ELSE 'excluded'
            END AS split
        FROM source.main.starterpacks
        WHERE date_created >= DATE '{config.minimum_date}'
          AND date_created <= DATE '{config.test_end}'
        """,
        force=force,
        details={
            "history_end": config.history_end,
            "train": [config.train_start, config.train_end],
            "validation": [config.validation_start, config.validation_end],
            "test": [config.test_start, config.test_end],
        },
    )
    run_sql_stage(
        connection,
        "history_memberships",
        "meta.history_memberships",
        f"""
        CREATE OR REPLACE TABLE meta.history_memberships AS
        SELECT DISTINCT
            m.pack_id::UINTEGER AS pack_id,
            m.member_id::UINTEGER AS member_id
        FROM source.main.starterpack_memberships AS m
        JOIN meta.pack_splits AS p USING (pack_id)
        WHERE p.split='history'
          AND m.date_added <= DATE '{config.history_end}'
        """,
        force=force,
    )
    run_sql_stage(
        connection,
        "history_nodes",
        "meta.history_nodes",
        """
        CREATE OR REPLACE TABLE meta.history_nodes AS
        WITH node_counts AS (
            SELECT member_id AS node_id, count(*)::UINTEGER AS history_pack_count
            FROM meta.history_memberships
            GROUP BY member_id
        )
        SELECT
            node_id::UINTEGER AS node_id,
            (row_number() OVER (ORDER BY node_id) - 1)::UINTEGER AS node_index,
            history_pack_count
        FROM node_counts
        ORDER BY node_id
        """,
        force=force,
    )
    run_sql_stage(
        connection,
        "history_packs",
        "meta.history_packs",
        """
        CREATE OR REPLACE TABLE meta.history_packs AS
        SELECT
            pack_id::UINTEGER AS pack_id,
            (row_number() OVER (ORDER BY pack_id) - 1)::UINTEGER AS pack_index,
            count(*)::UINTEGER AS pack_size
        FROM meta.history_memberships
        GROUP BY pack_id
        ORDER BY pack_id
        """,
        force=force,
    )
    run_sql_stage(
        connection,
        "hypergraph_incidence",
        "meta.hypergraph_incidence",
        """
        CREATE OR REPLACE TABLE meta.hypergraph_incidence AS
        SELECT n.node_index, p.pack_index
        FROM meta.history_memberships AS m
        JOIN meta.history_nodes AS n ON n.node_id=m.member_id
        JOIN meta.history_packs AS p USING (pack_id)
        ORDER BY p.pack_index, n.node_index
        """,
        force=force,
    )
    run_sql_stage(
        connection,
        "target_packs",
        "meta.target_packs",
        """
        CREATE OR REPLACE TABLE meta.target_packs AS
        SELECT pack_id, creator_id, date_created, split
        FROM meta.pack_splits
        WHERE split IN ('train', 'validation', 'test')
        """,
        force=force,
    )
    run_sql_stage(
        connection,
        "target_memberships",
        "meta.target_memberships",
        """
        CREATE OR REPLACE TABLE meta.target_memberships AS
        SELECT DISTINCT
            p.pack_id,
            m.member_id,
            n.node_index,
            n.node_index IS NOT NULL AS history_eligible
        FROM meta.target_packs AS p
        JOIN source.main.starterpack_memberships AS m USING (pack_id)
        LEFT JOIN meta.history_nodes AS n ON n.node_id=m.member_id
        WHERE m.date_added <= p.date_created
          AND m.member_id <> p.creator_id
        """,
        force=force,
        details={"label": "initial non-creator members only"},
    )
    run_sql_stage(
        connection,
        "target_pack_totals",
        "meta.target_pack_totals",
        """
        CREATE OR REPLACE TABLE meta.target_pack_totals AS
        SELECT
            p.pack_id,
            p.creator_id,
            p.date_created,
            p.split,
            count(m.member_id)::UINTEGER AS positive_members,
            count(m.member_id) FILTER (WHERE m.history_eligible)::UINTEGER AS eligible_positive_members
        FROM meta.target_packs AS p
        LEFT JOIN meta.target_memberships AS m USING (pack_id)
        GROUP BY p.pack_id, p.creator_id, p.date_created, p.split
        HAVING count(m.member_id) > 0
        """,
        force=force,
    )
    run_sql_stage(
        connection,
        "target_creators",
        "meta.target_creators",
        """
        CREATE OR REPLACE TABLE meta.target_creators AS
        SELECT DISTINCT creator_id FROM meta.target_pack_totals
        """,
        force=force,
    )


def split_summary(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            split,
            count(*)::UBIGINT AS packs,
            sum(positive_members)::UBIGINT AS positive_members,
            sum(eligible_positive_members)::UBIGINT AS eligible_positive_members,
            sum(eligible_positive_members)::DOUBLE / nullif(sum(positive_members), 0) AS eligibility_rate,
            min(date_created)::DATE AS first_date,
            max(date_created)::DATE AS last_date
        FROM meta.target_pack_totals
        GROUP BY split
        ORDER BY CASE split WHEN 'train' THEN 1 WHEN 'validation' THEN 2 ELSE 3 END
        """
    ).fetchall()
    return [
        {
            "split": row[0],
            "packs": int(row[1]),
            "positive_members": int(row[2]),
            "eligible_positive_members": int(row[3]),
            "eligibility_rate": float(row[4]),
            "first_date": row[5].isoformat(),
            "last_date": row[6].isoformat(),
        }
        for row in rows
    ]
