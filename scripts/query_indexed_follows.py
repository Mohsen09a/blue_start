from __future__ import annotations

import argparse
import time

from blue_start.duckdb_backend import connect


def run(connection: object, title: str, sql: str, parameters: list[int]) -> None:
    started = time.perf_counter()
    result = connection.execute(sql, parameters)
    rows = result.fetchall()
    elapsed = time.perf_counter() - started

    print(f"\n=== {title} ===")
    print(" | ".join(column[0] for column in result.description))
    for row in rows:
        print(" | ".join(str(value) for value in row))
    print(f"Rows shown: {len(rows)} | Time: {elapsed:.4f} seconds")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run fast person-level queries using the follow indexes."
    )
    parser.add_argument("--person-id", type=int, default=21_486_540)
    parser.add_argument("--other-person-id", type=int, default=3_528_659)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit must be between 1 and 1000")

    with connect(read_only=True) as connection:
        connection.execute("PRAGMA disable_progress_bar")

        run(
            connection,
            "Indexed outgoing follows",
            f"""
            SELECT dst AS followed_person_id, date_followed
            FROM follows_of(?)
            ORDER BY date_followed DESC
            LIMIT {args.limit}
            """,
            [args.person_id],
        )

        run(
            connection,
            "Indexed incoming followers",
            f"""
            SELECT src AS follower_id, date_followed
            FROM followers_of(?)
            ORDER BY date_followed DESC
            LIMIT {args.limit}
            """,
            [args.person_id],
        )

        run(
            connection,
            "Indexed edge-existence check",
            """
            SELECT is_following(?, ?) AS follows
            """,
            [args.person_id, args.other_person_id],
        )

        run(
            connection,
            "Indexed degree counts",
            """
            SELECT
                (SELECT count(*) FROM follows_of(?)) AS out_degree,
                (SELECT count(*) FROM followers_of(?)) AS in_degree
            """,
            [args.person_id, args.person_id],
        )

        run(
            connection,
            "Indexed mutual follows",
            f"""
            SELECT node_id
            FROM mutual_follows_of(?)
            LIMIT {args.limit}
            """,
            [args.person_id],
        )

        run(
            connection,
            "Indexed common follows",
            f"""
            SELECT node_id
            FROM common_follows_of(?, ?)
            LIMIT {args.limit}
            """,
            [args.person_id, args.other_person_id],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
