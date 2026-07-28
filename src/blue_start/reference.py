from __future__ import annotations

import json
import time
from pathlib import Path

from .duckdb_backend import connect, export_query, sql_path
from .paths import project_root
from .pipeline import RunResult
from .settings import load_settings


def upstream_root() -> Path:
    return project_root() / "reference" / "upstream-a-blue-start"


def import_upstream_reference() -> RunResult:
    """
    Import compact official outputs for algorithms that need hundreds of GB RAM.

    The imported values remain clearly namespaced as `reference_*`; they are not
    presented as results recomputed on the local machine.
    """
    root = upstream_root()
    data = root / "data"
    required = (
        "follows_sccs.csv.gz",
        "follows_wccs.csv.gz",
        "s_count.csv",
        "starterpack_k_core.csv.gz",
        "starterpack_pair_cooccurrence.csv.gz",
    )
    missing = [name for name in required if not (data / name).exists()]
    if missing:
        raise FileNotFoundError(
            "Missing upstream reference artifacts: " + ", ".join(missing)
        )

    settings = load_settings()
    started = time.perf_counter()
    outputs: list[str] = []
    with connect(settings) as con:
        for kind in ("scc", "wcc"):
            path = sql_path(data / f"follows_{kind}s.csv.gz")
            con.execute(
                f"""
                CREATE OR REPLACE TABLE results.reference_follow_{kind}_distribution AS
                SELECT
                    component_size,
                    count(*)::UBIGINT AS component_count
                FROM read_csv(
                    {path},
                    header = false,
                    columns = {{'component_size': 'UINTEGER'}}
                )
                GROUP BY component_size
                ORDER BY component_size
                """
            )
            output = (
                settings.parquet_outputs
                / f"reference_follow_{kind}_distribution.parquet"
            )
            export_query(
                con,
                f"SELECT * FROM results.reference_follow_{kind}_distribution",
                output,
            )
            outputs.append(str(output))

        s_count = sql_path(data / "s_count.csv")
        con.execute(
            f"""
            CREATE OR REPLACE TABLE results.reference_s_line_counts AS
            SELECT s::UINTEGER AS s, nodes::UINTEGER AS nodes, edges::UBIGINT AS edges
            FROM read_csv({s_count}, header = true)
            """
        )
        s_output = settings.parquet_outputs / "reference_s_line_counts.parquet"
        export_query(
            con,
            "SELECT * FROM results.reference_s_line_counts ORDER BY s",
            s_output,
        )
        outputs.append(str(s_output))

        kcore = sql_path(data / "starterpack_k_core.csv.gz")
        con.execute(
            f"""
            CREATE OR REPLACE TABLE results.reference_kcore_distribution AS
            SELECT core_number, count(*)::UBIGINT AS node_count
            FROM read_csv(
                {kcore},
                header = false,
                columns = {{'core_number': 'UINTEGER'}}
            )
            GROUP BY core_number
            ORDER BY core_number
            """
        )
        kcore_output = settings.parquet_outputs / "reference_kcore_distribution.parquet"
        export_query(
            con,
            "SELECT * FROM results.reference_kcore_distribution ORDER BY core_number",
            kcore_output,
        )
        outputs.append(str(kcore_output))

        cooccurrence = sql_path(data / "starterpack_pair_cooccurrence.csv.gz")
        con.execute(
            f"""
            CREATE OR REPLACE TABLE results.reference_pair_cooccurrence_distribution AS
            SELECT cooccurrence, count(*)::UBIGINT AS pair_count
            FROM read_csv(
                {cooccurrence},
                header = false,
                columns = {{'cooccurrence': 'UINTEGER'}}
            )
            GROUP BY cooccurrence
            ORDER BY cooccurrence
            """
        )
        pair_output = (
            settings.parquet_outputs
            / "reference_pair_cooccurrence_distribution.parquet"
        )
        export_query(
            con,
            """
            SELECT *
            FROM results.reference_pair_cooccurrence_distribution
            ORDER BY cooccurrence
            """,
            pair_output,
        )
        outputs.append(str(pair_output))

        largest_scc = con.execute(
            "SELECT max(component_size) FROM results.reference_follow_scc_distribution"
        ).fetchone()[0]
        largest_wcc = con.execute(
            "SELECT max(component_size) FROM results.reference_follow_wcc_distribution"
        ).fetchone()[0]
        kcore_1000 = con.execute(
            """
            SELECT sum(node_count)
            FROM results.reference_kcore_distribution
            WHERE core_number >= 1000
            """
        ).fetchone()[0]

    summary = {
        "source": "https://github.com/nwlandry/a-blue-start",
        "largest_follow_scc": largest_scc,
        "largest_follow_wcc": largest_wcc,
        "nodes_with_kcore_at_least_1000": kcore_1000,
        "community_labels": str(data / "node_labels.json"),
        "edge_entropy": str(data / "edge_entropy.json"),
        "warning": "These are official upstream artifacts, not locally recomputed values.",
    }
    result = RunResult(
        task="reference_import",
        seconds=time.perf_counter() - started,
        outputs=outputs,
        summary=summary,
    )
    summary_path = settings.summary_outputs / "reference_import.json"
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

