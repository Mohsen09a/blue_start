from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

from blue_start.duckdb_backend import connect


def run_query(
    connection: object,
    title: str,
    sql: str,
    parameters: Sequence[object] = (),
) -> None:
    print(f"\n=== {title} ===")
    started = time.perf_counter()
    result = connection.execute(sql, parameters)
    rows = result.fetchall()
    elapsed = time.perf_counter() - started

    columns = [item[0] for item in result.description]
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(str(value) for value in row))
    print(f"Rows: {len(rows)} | Time: {elapsed:.3f} seconds")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run timed, read-only example queries against Blue Start DuckDB."
    )
    parser.add_argument(
        "--person-id",
        type=int,
        default=21_486_540,
        help="deidentified numeric person ID (default: 21486540)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="maximum rows shown by adjacency queries (default: 20)",
    )
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit must be between 1 and 1000")

    with connect(read_only=True) as connection:
        connection.execute("PRAGMA disable_progress_bar")
        run_query(
            connection,
            "Person and cached degree summary (fast)",
            """
            SELECT
                n.node_id,
                n.date_created,
                n.active,
                n.status,
                d.in_degree,
                d.out_degree
            FROM nodes AS n
            LEFT JOIN results.follow_degrees_full AS d USING (node_id)
            WHERE n.node_id = ?
            """,
            [args.person_id],
        )

        run_query(
            connection,
            "People this person follows (full edge scan)",
            f"""
            SELECT
                f.dst AS followed_person_id,
                f.date_followed,
                n.date_created,
                n.active,
                n.status
            FROM follows AS f
            LEFT JOIN nodes AS n ON n.node_id = f.dst
            WHERE f.src = ?
            ORDER BY f.date_followed DESC
            LIMIT {args.limit}
            """,
            [args.person_id],
        )

        run_query(
            connection,
            "People who follow this person (full edge scan)",
            f"""
            SELECT
                f.src AS follower_id,
                f.date_followed,
                n.date_created,
                n.active,
                n.status
            FROM follows AS f
            LEFT JOIN nodes AS n ON n.node_id = f.src
            WHERE f.dst = ?
            ORDER BY f.date_followed DESC
            LIMIT {args.limit}
            """,
            [args.person_id],
        )

        run_query(
            connection,
            "Starter Packs containing this person",
            """
            SELECT
                m.pack_id,
                m.date_added,
                p.creator_id,
                p.date_created AS pack_created,
                p.member_count
            FROM starterpack_memberships AS m
            JOIN starterpacks AS p USING (pack_id)
            WHERE m.member_id = ?
            ORDER BY m.date_added DESC
            LIMIT 20
            """,
            [args.person_id],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
