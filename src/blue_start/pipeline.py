from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .duckdb_backend import connect, export_query, sql_path
from .paths import resolved_datasets
from .settings import DuckDBSettings, load_settings


@dataclass(frozen=True)
class RunResult:
    task: str
    seconds: float
    outputs: list[str]
    summary: dict[str, Any]


def _require_paths(*keys: str) -> dict[str, Path]:
    datasets = resolved_datasets()
    missing = [key for key in keys if datasets.get(key) is None]
    if missing:
        raise FileNotFoundError(f"Missing datasets: {', '.join(missing)}")
    return {key: datasets[key] for key in keys}  # type: ignore[return-value]


def _write_summary(settings: DuckDBSettings, result: RunResult) -> Path:
    path = settings.summary_outputs / f"{result.task}.json"
    path.write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def prepare_database(*, rebuild: bool = False) -> RunResult:
    settings = load_settings()
    paths = _require_paths(
        "nodes_csv",
        "starterpacks_jsonl",
        "follows_parquet",
    )
    started = time.perf_counter()

    with connect(settings) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS results")
        con.execute("CREATE SCHEMA IF NOT EXISTS meta")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS meta.pipeline_runs (
                task VARCHAR,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                details JSON
            )
            """
        )

        follows = sql_path(paths["follows_parquet"])
        con.execute(
            f"""
            CREATE OR REPLACE VIEW follows AS
            SELECT
                "from"::UINTEGER AS src,
                "to"::UINTEGER AS dst,
                date_followed::DATE AS date_followed
            FROM read_parquet({follows})
            """
        )

        table_count = con.execute(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_name IN ('nodes', 'starterpacks', 'starterpack_memberships')
              AND table_type = 'BASE TABLE'
            """
        ).fetchone()[0]

        if rebuild or table_count != 3:
            nodes = sql_path(paths["nodes_csv"])
            con.execute(
                f"""
                CREATE OR REPLACE TABLE nodes AS
                SELECT
                    id::UINTEGER AS node_id,
                    "date-created"::DATE AS date_created,
                    active::BOOLEAN AS active,
                    status::VARCHAR AS status
                FROM read_csv(
                    {nodes},
                    header = true,
                    columns = {{
                        'id': 'UINTEGER',
                        'date-created': 'DATE',
                        'active': 'BOOLEAN',
                        'status': 'VARCHAR'
                    }},
                    nullstr = ''
                )
                """
            )

            packs = sql_path(paths["starterpacks_jsonl"])
            source = (
                f"read_json_auto({packs}, format = 'newline_delimited', "
                "maximum_object_size = 16777216)"
            )
            con.execute(
                f"""
                CREATE OR REPLACE TABLE starterpacks AS
                SELECT
                    "pack-id"::UINTEGER AS pack_id,
                    "creator-id"::UINTEGER AS creator_id,
                    "date-created"::DATE AS date_created,
                    len(members)::UINTEGER AS member_count
                FROM {source}
                """
            )
            con.execute(
                f"""
                CREATE OR REPLACE TABLE starterpack_memberships AS
                SELECT
                    pack_id::UINTEGER AS pack_id,
                    id::UINTEGER AS member_id,
                    "date-added"::DATE AS date_added
                FROM (
                    SELECT
                        "pack-id" AS pack_id,
                        unnest(members, recursive := true)
                    FROM {source}
                )
                """
            )

        counts = {
            "nodes": con.execute("SELECT count(*) FROM nodes").fetchone()[0],
            "starterpacks": con.execute(
                "SELECT count(*) FROM starterpacks"
            ).fetchone()[0],
            "memberships": con.execute(
                "SELECT count(*) FROM starterpack_memberships"
            ).fetchone()[0],
            "follow_edges": con.execute(
                f"SELECT num_rows FROM parquet_file_metadata({follows})"
            ).fetchone()[0],
        }
        con.execute(
            """
            INSERT INTO meta.pipeline_runs
            VALUES ('prepare', current_timestamp, current_timestamp, ?)
            """,
            [json.dumps(counts)],
        )

    result = RunResult(
        task="prepare",
        seconds=time.perf_counter() - started,
        outputs=[str(settings.database)],
        summary=counts,
    )
    summary_path = _write_summary(settings, result)
    return RunResult(
        task=result.task,
        seconds=result.seconds,
        outputs=result.outputs + [str(summary_path)],
        summary=result.summary,
    )


def analyze_nodes() -> RunResult:
    settings = load_settings()
    started = time.perf_counter()
    outputs: list[str] = []
    with connect(settings) as con:
        con.execute(
            """
            CREATE OR REPLACE TABLE results.node_creation_volume AS
            SELECT date_created, count(*)::UBIGINT AS account_count
            FROM nodes
            WHERE date_created IS NOT NULL
            GROUP BY date_created
            ORDER BY date_created
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE results.node_status_counts AS
            SELECT active, status, count(*)::UBIGINT AS account_count
            FROM nodes
            GROUP BY active, status
            ORDER BY account_count DESC
            """
        )
        creation_path = settings.parquet_outputs / "node_creation_volume.parquet"
        status_path = settings.parquet_outputs / "node_status_counts.parquet"
        export_query(con, "SELECT * FROM results.node_creation_volume", creation_path)
        export_query(con, "SELECT * FROM results.node_status_counts", status_path)
        outputs.extend([str(creation_path), str(status_path)])
        total, active, extant, unknown = con.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE active),
                count(*) FILTER (WHERE active OR active IS NULL),
                count(*) FILTER (WHERE active IS NULL)
            FROM nodes
            """
        ).fetchone()
        summary = {
            "total_nodes": total,
            "active_nodes": active,
            "extant_nodes": extant,
            "unknown_activity": unknown,
        }

    result = RunResult(
        task="nodes",
        seconds=time.perf_counter() - started,
        outputs=outputs,
        summary=summary,
    )
    summary_path = _write_summary(settings, result)
    return RunResult(
        result.task,
        result.seconds,
        result.outputs + [str(summary_path)],
        result.summary,
    )


def _follow_source(row_limit: int | None) -> tuple[str, str]:
    if row_limit is None:
        return "follows", "full"
    if row_limit < 1:
        raise ValueError("row_limit must be positive")
    return f"(SELECT * FROM follows LIMIT {int(row_limit)})", f"sample_{row_limit}"


def analyze_following(
    *,
    row_limit: int | None = None,
    include_time_std: bool = False,
    include_impossible_timestamps: bool = False,
    force: bool = False,
) -> RunResult:
    settings = load_settings()
    started = time.perf_counter()
    source, label = _follow_source(row_limit)
    outputs: list[str] = []

    with connect(settings) as con:
        existing = {
            row[0]
            for row in con.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'results'
                """
            ).fetchall()
        }
        volume_table = f"follow_volume_{label}"
        degrees_table = f"follow_degrees_{label}"
        if force or volume_table not in existing:
            con.execute(
                f"""
                CREATE OR REPLACE TABLE results.{volume_table} AS
                SELECT date_followed, count(*)::UBIGINT AS follow_count
                FROM {source}
                GROUP BY date_followed
                ORDER BY date_followed
                """
            )
        if force or degrees_table not in existing:
            con.execute(
                f"""
                CREATE OR REPLACE TABLE results.{degrees_table} AS
                WITH
                out_degree AS (
                    SELECT src AS node_id, count(*)::UBIGINT AS out_degree
                    FROM {source}
                    GROUP BY src
                ),
                in_degree AS (
                    SELECT dst AS node_id, count(*)::UBIGINT AS in_degree
                    FROM {source}
                    GROUP BY dst
                )
                SELECT
                    coalesce(i.node_id, o.node_id)::UINTEGER AS node_id,
                    coalesce(i.in_degree, 0)::UBIGINT AS in_degree,
                    coalesce(o.out_degree, 0)::UBIGINT AS out_degree
                FROM in_degree i
                FULL OUTER JOIN out_degree o USING (node_id)
                """
            )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE results.follow_degree_distribution_{label} AS
            WITH in_dist AS (
                SELECT in_degree AS degree, count(*)::UBIGINT AS node_count
                FROM results.{degrees_table}
                WHERE in_degree > 0
                GROUP BY in_degree
            ),
            out_dist AS (
                SELECT out_degree AS degree, count(*)::UBIGINT AS node_count
                FROM results.{degrees_table}
                WHERE out_degree > 0
                GROUP BY out_degree
            )
            SELECT
                coalesce(i.degree, o.degree)::UBIGINT AS degree,
                coalesce(i.node_count, 0)::UBIGINT AS in_node_count,
                coalesce(o.node_count, 0)::UBIGINT AS out_node_count
            FROM in_dist i
            FULL OUTER JOIN out_dist o USING (degree)
            ORDER BY degree
            """
        )

        volume_path = settings.parquet_outputs / f"follow_volume_{label}.parquet"
        degrees_path = settings.parquet_outputs / f"follow_degrees_{label}.parquet"
        degree_distribution_path = (
            settings.parquet_outputs / f"follow_degree_distribution_{label}.parquet"
        )
        export_query(
            con,
            f"SELECT * FROM results.follow_volume_{label}",
            volume_path,
        )
        export_query(
            con,
            f"SELECT * FROM results.follow_degrees_{label}",
            degrees_path,
        )
        export_query(
            con,
            f"SELECT * FROM results.follow_degree_distribution_{label}",
            degree_distribution_path,
        )
        outputs.extend(
            [str(volume_path), str(degrees_path), str(degree_distribution_path)]
        )

        if include_time_std:
            con.execute(
                f"""
                CREATE OR REPLACE TABLE results.follow_time_std_{label} AS
                WITH
                incoming AS (
                    SELECT
                        dst AS node_id,
                        stddev_samp(epoch(date_followed) / 86400.0) AS in_std_days
                    FROM {source}
                    GROUP BY dst
                ),
                outgoing AS (
                    SELECT
                        src AS node_id,
                        stddev_samp(epoch(date_followed) / 86400.0) AS out_std_days
                    FROM {source}
                    GROUP BY src
                )
                SELECT
                    coalesce(i.node_id, o.node_id)::UINTEGER AS node_id,
                    i.in_std_days,
                    o.out_std_days
                FROM incoming i
                FULL OUTER JOIN outgoing o USING (node_id)
                """
            )
            time_path = settings.parquet_outputs / f"follow_time_std_{label}.parquet"
            export_query(
                con,
                f"SELECT * FROM results.follow_time_std_{label}",
                time_path,
            )
            outputs.append(str(time_path))

        impossible = None
        if include_impossible_timestamps:
            impossible = con.execute(
                f"""
                SELECT count(*)
                FROM {source} f
                LEFT JOIN nodes src ON src.node_id = f.src
                LEFT JOIN nodes dst ON dst.node_id = f.dst
                WHERE f.date_followed < src.date_created
                   OR f.date_followed < dst.date_created
                """
            ).fetchone()[0]

        edge_count, node_count, max_in, max_out = con.execute(
            f"""
            SELECT
                (SELECT sum(follow_count) FROM results.follow_volume_{label}),
                count(*),
                max(in_degree),
                max(out_degree)
            FROM results.follow_degrees_{label}
            """
        ).fetchone()
        summary = {
            "profile": label,
            "edge_count": edge_count,
            "nodes_with_any_follow_edge": node_count,
            "maximum_in_degree": max_in,
            "maximum_out_degree": max_out,
            "impossible_timestamp_count": impossible,
        }

    result = RunResult(
        task=f"following_{label}",
        seconds=time.perf_counter() - started,
        outputs=outputs,
        summary=summary,
    )
    summary_path = _write_summary(settings, result)
    return RunResult(
        result.task,
        result.seconds,
        result.outputs + [str(summary_path)],
        result.summary,
    )


def analyze_starterpacks() -> RunResult:
    settings = load_settings()
    started = time.perf_counter()
    outputs: list[str] = []
    with connect(settings) as con:
        con.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_degree_distribution AS
            WITH degrees AS (
                SELECT member_id, count(*)::UINTEGER AS degree
                FROM starterpack_memberships
                GROUP BY member_id
            )
            SELECT degree, count(*)::UBIGINT AS node_count
            FROM degrees
            GROUP BY degree
            ORDER BY degree
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_size_distribution AS
            SELECT member_count AS pack_size, count(*)::UBIGINT AS pack_count
            FROM starterpacks
            GROUP BY member_count
            ORDER BY member_count
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_creation_volume AS
            SELECT date_created, count(*)::UBIGINT AS pack_count
            FROM starterpacks
            GROUP BY date_created
            ORDER BY date_created
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE results.starterpacks_created_per_user AS
            SELECT creator_id, count(*)::UINTEGER AS packs_created
            FROM starterpacks
            GROUP BY creator_id
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE results.starterpack_creator_ages AS
            SELECT
                p.pack_id,
                date_diff('day', n.date_created, p.date_created)::INTEGER
                    AS account_age_days
            FROM starterpacks p
            LEFT JOIN nodes n ON n.node_id = p.creator_id
            WHERE n.date_created IS NOT NULL
            """
        )

        table_names = (
            "starterpack_degree_distribution",
            "starterpack_size_distribution",
            "starterpack_creation_volume",
            "starterpacks_created_per_user",
            "starterpack_creator_ages",
        )
        for table in table_names:
            path = settings.parquet_outputs / f"{table}.parquet"
            export_query(con, f"SELECT * FROM results.{table}", path)
            outputs.append(str(path))

        summary_row = con.execute(
            """
            WITH degrees AS (
                SELECT member_id, count(*)::UBIGINT AS degree
                FROM starterpack_memberships
                GROUP BY member_id
            )
            SELECT
                (SELECT count(*) FROM starterpacks),
                count(*),
                min(degree),
                max(degree),
                avg(degree),
                (SELECT min(member_count) FROM starterpacks),
                (SELECT max(member_count) FROM starterpacks),
                (SELECT avg(member_count) FROM starterpacks),
                (SELECT count(*) FROM results.starterpack_creator_ages
                 WHERE account_age_days < 0)
            FROM degrees
            """
        ).fetchone()
        summary = dict(
            zip(
                (
                    "starterpack_count",
                    "member_node_count",
                    "minimum_degree",
                    "maximum_degree",
                    "mean_degree",
                    "minimum_pack_size",
                    "maximum_pack_size",
                    "mean_pack_size",
                    "negative_creator_age_count",
                ),
                summary_row,
            )
        )

    result = RunResult(
        task="starterpacks",
        seconds=time.perf_counter() - started,
        outputs=outputs,
        summary=summary,
    )
    summary_path = _write_summary(settings, result)
    return RunResult(
        result.task,
        result.seconds,
        result.outputs + [str(summary_path)],
        result.summary,
    )


def doctor() -> dict[str, Any]:
    settings = load_settings()
    paths = resolved_datasets()
    report: dict[str, Any] = {
        "duckdb": {},
        "paths": {},
        "warnings": [],
    }
    for key, path in paths.items():
        report["paths"][key] = str(path) if path else None

    try:
        with connect(settings) as con:
            version = con.execute("SELECT version()").fetchone()[0]
            values = con.execute(
                """
                SELECT
                    current_setting('memory_limit'),
                    current_setting('threads'),
                    current_setting('temp_directory'),
                    current_setting('max_temp_directory_size'),
                    current_setting('preserve_insertion_order')
                """
            ).fetchone()
            report["duckdb"] = {
                "version": version,
                "memory_limit": values[0],
                "threads": values[1],
                "temp_directory": values[2],
                "max_temp_directory_size": values[3],
                "preserve_insertion_order": values[4],
                "database": str(settings.database),
            }
    except Exception as exc:
        report["warnings"].append(str(exc))

    if any(path is None for path in paths.values()):
        report["warnings"].append("One or more datasets are missing.")
    return report
