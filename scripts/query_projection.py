from __future__ import annotations

import argparse
import time

from blue_start.duckdb_backend import connect
from blue_start.settings import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Look up one weighted edge in the Starter Pack projection."
    )
    parser.add_argument("first_id", type=int)
    parser.add_argument("second_id", type=int)
    args = parser.parse_args()

    node_a = min(args.first_id, args.second_id)
    node_b = max(args.first_id, args.second_id)
    settings = load_settings()
    started = time.perf_counter()
    with connect(settings, read_only=True) as connection:
        pair_bucket = connection.execute(
            "SELECT hash(?, ?) % 256",
            [node_a, node_b],
        ).fetchone()[0]
        row = connection.execute(
            """
            SELECT cooccurrence
            FROM starterpack_clique_projection
            WHERE pair_bucket = ?
              AND node_a = ?
              AND node_b = ?
            """,
            [pair_bucket, node_a, node_b],
        ).fetchone()

    elapsed = time.perf_counter() - started
    print(f"node_a: {node_a}")
    print(f"node_b: {node_b}")
    print(f"pair_bucket: {pair_bucket}")
    print(f"cooccurrence: {0 if row is None else row[0]}")
    print(f"elapsed_seconds: {elapsed:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
