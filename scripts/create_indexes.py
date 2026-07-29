from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from blue_start.duckdb_backend import connect


@dataclass(frozen=True)
class IndexSpec:
    name: str
    schema: str
    table: str
    columns: tuple[str, ...]


# ART indexes are useful for highly selective point lookups. They are not meant
# to replace DuckDB's scan-oriented execution for large analytical queries.
CORE_INDEXES = (
    IndexSpec("idx_nodes_node_id", "main", "nodes", ("node_id",)),
    IndexSpec("idx_starterpacks_pack_id", "main", "starterpacks", ("pack_id",)),
    IndexSpec(
        "idx_starterpacks_creator_id",
        "main",
        "starterpacks",
        ("creator_id",),
    ),
    IndexSpec(
        "idx_memberships_member_id",
        "main",
        "starterpack_memberships",
        ("member_id",),
    ),
    IndexSpec(
        "idx_memberships_pack_id",
        "main",
        "starterpack_memberships",
        ("pack_id",),
    ),
)

RESULT_INDEXES = (
    IndexSpec(
        "idx_follow_degrees_full_node_id",
        "results",
        "follow_degrees_full",
        ("node_id",),
    ),
    IndexSpec(
        "idx_follow_time_std_full_node_id",
        "results",
        "follow_time_std_full",
        ("node_id",),
    ),
)


def table_exists(connection: object, spec: IndexSpec) -> bool:
    return bool(
        connection.execute(
            """
            SELECT count(*) > 0
            FROM information_schema.tables
            WHERE table_schema = ?
              AND table_name = ?
              AND table_type = 'BASE TABLE'
            """,
            [spec.schema, spec.table],
        ).fetchone()[0]
    )


def create_index(connection: object, spec: IndexSpec) -> None:
    columns = ", ".join(spec.columns)
    statement = (
        f"CREATE INDEX IF NOT EXISTS {spec.name} "
        f"ON {spec.schema}.{spec.table} ({columns})"
    )
    started = time.perf_counter()
    connection.execute(statement)
    elapsed = time.perf_counter() - started
    print(
        f"[OK] {spec.name}: {spec.schema}.{spec.table}({columns}) "
        f"in {elapsed:.3f} seconds"
    )


def print_indexes(connection: object) -> None:
    rows = connection.execute(
        """
        SELECT
            schema_name,
            table_name,
            index_name,
            expressions,
            is_unique
        FROM duckdb_indexes()
        ORDER BY schema_name, table_name, index_name
        """
    ).fetchall()

    if not rows:
        print("No explicit indexes exist.")
        return

    print("\nExisting indexes:")
    for schema, table, name, expressions, unique in rows:
        print(
            f"  {name}: {schema}.{table} {expressions}"
            f"{' UNIQUE' if unique else ''}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create useful ART indexes in the Blue Start DuckDB database."
    )
    parser.add_argument(
        "--include-results",
        action="store_true",
        help="also index optional full per-node result tables",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="show existing indexes without creating anything",
    )
    args = parser.parse_args()

    with connect() as connection:
        connection.execute("PRAGMA disable_progress_bar")

        if not args.list_only:
            specs = CORE_INDEXES + (RESULT_INDEXES if args.include_results else ())
            for spec in specs:
                if table_exists(connection, spec):
                    create_index(connection, spec)
                else:
                    print(
                        f"[SKIP] {spec.schema}.{spec.table} does not exist; "
                        f"{spec.name} was not created"
                    )

        print_indexes(connection)

    print(
        "\nNote: follows is an external Parquet view and cannot receive a "
        "DuckDB ART index."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
