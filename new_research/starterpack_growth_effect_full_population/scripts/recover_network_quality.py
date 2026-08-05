from __future__ import annotations

import json
import math
import time
from pathlib import Path

from blue_start.duckdb_backend import connect, export_query
from blue_start.settings import load_settings


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.toml"
CHECKPOINTS = ROOT / "work" / "network_quality_batches"
OUTPUT = ROOT / "outputs" / "parquet" / "starterpack_growth_network_quality.parquet"


def main() -> int:
    CHECKPOINTS.mkdir(parents=True, exist_ok=True)
    settings = load_settings(CONFIG)
    started = time.perf_counter()
    totals = {
        role: {
            "new_followers_90": 0,
            "reciprocal_new_followers_90": 0,
            "community_known_pairs": 0,
            "same_community_pairs": 0,
        }
        for role in ("control", "treated")
    }
    with connect(settings) as con:
        con.execute("SET memory_limit = '8GB'")
        con.execute("SET threads = 4")
        con.execute("SET max_temp_directory_size = '20GB'")
        for bucket in range(256):
            checkpoint = CHECKPOINTS / f"bucket_{bucket:03d}.json"
            if checkpoint.exists():
                rows = json.loads(checkpoint.read_text(encoding="utf-8"))
            else:
                rows = [
                    {
                        "role": str(row[0]),
                        "new_followers_90": int(row[1]),
                        "reciprocal_new_followers_90": int(row[2]),
                        "community_known_pairs": int(row[3]),
                        "same_community_pairs": int(row[4]),
                    }
                    for row in con.execute(
                        f"""
                        WITH subjects AS (
                            SELECT match_id, 'treated' AS role,
                                   treated_node_id AS node_id,
                                   treated_index_date AS index_date,
                                   treated_final_community AS subject_community
                            FROM results.starterpack_growth_matched_cohort
                            WHERE hash(treated_node_id) % 256 = {bucket}
                            UNION ALL
                            SELECT match_id, 'control' AS role,
                                   control_node_id AS node_id,
                                   control_index_date AS index_date,
                                   control_final_community AS subject_community
                            FROM results.starterpack_growth_matched_cohort
                            WHERE hash(control_node_id) % 256 = {bucket}
                        ), incoming AS (
                            SELECT s.match_id, s.role, s.node_id, s.index_date,
                                   s.subject_community, f.src::UINTEGER AS follower_id
                            FROM subjects AS s
                            JOIN indexed_follows_by_dst AS f
                              ON f.dst_bucket = {bucket} AND f.dst = s.node_id
                            WHERE f.date_followed > s.index_date
                              AND f.date_followed <= s.index_date + INTERVAL 90 DAY
                            GROUP BY s.match_id, s.role, s.node_id, s.index_date,
                                     s.subject_community, f.src
                        ), quality AS (
                            SELECT incoming.match_id, incoming.role,
                                   incoming.follower_id,
                                   bool_or(reverse_edge.dst IS NOT NULL) AS reciprocal,
                                   incoming.subject_community,
                                   follower_label.community AS follower_community
                            FROM incoming
                            LEFT JOIN indexed_follows_by_src AS reverse_edge
                              ON reverse_edge.src_bucket = {bucket}
                             AND reverse_edge.src = incoming.node_id
                             AND reverse_edge.dst = incoming.follower_id
                             AND reverse_edge.date_followed >= DATE '2022-11-17'
                             AND reverse_edge.date_followed
                                 <= incoming.index_date + INTERVAL 90 DAY
                            LEFT JOIN results.starterpack_leiden_labels_local AS follower_label
                              ON follower_label.node_id = incoming.follower_id
                            GROUP BY incoming.match_id, incoming.role,
                                     incoming.follower_id, incoming.subject_community,
                                     follower_label.community
                        )
                        SELECT role,
                               count(*)::UBIGINT,
                               count(*) FILTER (WHERE reciprocal)::UBIGINT,
                               count(*) FILTER (
                                   WHERE subject_community IS NOT NULL
                                     AND follower_community IS NOT NULL
                               )::UBIGINT,
                               count(*) FILTER (
                                   WHERE subject_community = follower_community
                               )::UBIGINT
                        FROM quality
                        GROUP BY role
                        ORDER BY role
                        """
                    ).fetchall()
                ]
                temporary = checkpoint.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(rows, indent=2), encoding="utf-8")
                temporary.replace(checkpoint)
            for row in rows:
                role = row["role"]
                for key in totals[role]:
                    totals[role][key] += int(row[key])
            print(f"[OK] network-quality bucket {bucket + 1}/256", flush=True)

        con.execute("DROP TABLE IF EXISTS results.starterpack_growth_network_quality")
        con.execute(
            """
            CREATE TABLE results.starterpack_growth_network_quality (
                role VARCHAR,
                new_followers_90 UBIGINT,
                reciprocal_new_followers_90 UBIGINT,
                reciprocal_share DOUBLE,
                community_known_pairs UBIGINT,
                same_final_community_share DOUBLE
            )
            """
        )
        rows = []
        for role in ("control", "treated"):
            values = totals[role]
            new = values["new_followers_90"]
            known = values["community_known_pairs"]
            rows.append(
                (
                    role,
                    new,
                    values["reciprocal_new_followers_90"],
                    values["reciprocal_new_followers_90"] / new if new else math.nan,
                    known,
                    values["same_community_pairs"] / known if known else math.nan,
                )
            )
        con.executemany(
            "INSERT INTO results.starterpack_growth_network_quality VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        export_query(
            con,
            "SELECT * FROM results.starterpack_growth_network_quality ORDER BY role",
            OUTPUT,
        )
    print(json.dumps({"seconds": time.perf_counter() - started, "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
