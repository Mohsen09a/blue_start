from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from blue_start.duckdb_backend import connect, sql_path
from blue_start.paths import project_root


INDEX_ROOT = project_root() / "work" / "follow_indexes"


def _safe_remove(path: Path) -> None:
    root = INDEX_ROOT.resolve()
    target = path.resolve()
    if target.parent != root:
        raise RuntimeError(f"Refusing to remove unexpected path: {target}")
    if target.exists():
        shutil.rmtree(target)


def _directory_size(path: Path) -> tuple[int, int]:
    files = list(path.rglob("*.parquet"))
    return len(files), sum(item.stat().st_size for item in files)


def _has_parquet(path: Path) -> bool:
    return path.exists() and next(path.rglob("*.parquet"), None) is not None


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def _build_index(
    connection: object,
    *,
    direction: str,
    partitions: int,
    rebuild: bool,
) -> None:
    if direction not in {"src", "dst"}:
        raise ValueError(f"Unsupported direction: {direction}")

    bucket = f"{direction}_bucket"
    output = INDEX_ROOT / f"by_{direction}"

    if _has_parquet(output):
        if not rebuild:
            print(
                f"[SKIP] {output} already contains an index. "
                "Use --rebuild to replace it."
            )
            return
        _safe_remove(output)

    output.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"[BUILD] by_{direction}: {partitions} partitions from the full "
        "2.4-billion-edge relation"
    )
    started = time.perf_counter()
    connection.execute(
        f"""
        COPY (
            SELECT
                src,
                dst,
                date_followed,
                (hash({direction}) % {partitions})::USMALLINT AS {bucket}
            FROM follows
        )
        TO {sql_path(output)}
        (
            FORMAT PARQUET,
            PARTITION_BY ({bucket}),
            COMPRESSION ZSTD,
            ROW_GROUP_SIZE 122880
        )
        """
    )
    elapsed = time.perf_counter() - started
    file_count, byte_count = _directory_size(output)
    print(
        f"[OK] by_{direction}: {file_count} Parquet files, "
        f"{_format_size(byte_count)}, {elapsed:.2f} seconds"
    )


def _install_query_macros(connection: object, partitions: int) -> None:
    src_glob = sql_path(INDEX_ROOT / "by_src" / "**" / "*.parquet")
    dst_glob = sql_path(INDEX_ROOT / "by_dst" / "**" / "*.parquet")

    if _has_parquet(INDEX_ROOT / "by_src"):
        connection.execute(
            f"""
            CREATE OR REPLACE VIEW indexed_follows_by_src AS
            SELECT
                src::UINTEGER AS src,
                dst::UINTEGER AS dst,
                date_followed::DATE AS date_followed,
                src_bucket::USMALLINT AS src_bucket
            FROM read_parquet({src_glob}, hive_partitioning = true)
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE MACRO follows_of(p_person_id) AS TABLE
            SELECT dst, date_followed
            FROM indexed_follows_by_src
            WHERE src_bucket =
                    (hash(p_person_id::UINTEGER) % {partitions})::USMALLINT
              AND src = p_person_id::UINTEGER
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE MACRO is_following(
                p_source_id,
                p_destination_id
            ) AS (
                SELECT count(*) > 0
                FROM indexed_follows_by_src
                WHERE src_bucket =
                        (hash(p_source_id::UINTEGER) % {partitions})::USMALLINT
                  AND src = p_source_id::UINTEGER
                  AND dst = p_destination_id::UINTEGER
            )
            """
        )
        print("[OK] Installed follows_of(person_id) and is_following(src, dst)")

    if _has_parquet(INDEX_ROOT / "by_dst"):
        connection.execute(
            f"""
            CREATE OR REPLACE VIEW indexed_follows_by_dst AS
            SELECT
                src::UINTEGER AS src,
                dst::UINTEGER AS dst,
                date_followed::DATE AS date_followed,
                dst_bucket::USMALLINT AS dst_bucket
            FROM read_parquet({dst_glob}, hive_partitioning = true)
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE MACRO followers_of(p_person_id) AS TABLE
            SELECT src, date_followed
            FROM indexed_follows_by_dst
            WHERE dst_bucket =
                    (hash(p_person_id::UINTEGER) % {partitions})::USMALLINT
              AND dst = p_person_id::UINTEGER
            """
        )
        print("[OK] Installed followers_of(person_id)")

    if _has_parquet(INDEX_ROOT / "by_src") and _has_parquet(
        INDEX_ROOT / "by_dst"
    ):
        connection.execute(
            """
            CREATE OR REPLACE MACRO mutual_follows_of(p_person_id) AS TABLE
            SELECT outgoing.dst AS node_id
            FROM follows_of(p_person_id) AS outgoing
            JOIN followers_of(p_person_id) AS incoming
              ON incoming.src = outgoing.dst
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE MACRO common_follows_of(
                p_first_person_id,
                p_second_person_id
            ) AS TABLE
            SELECT first_person.dst AS node_id
            FROM follows_of(p_first_person_id) AS first_person
            JOIN follows_of(p_second_person_id) AS second_person
              ON second_person.dst = first_person.dst
            """
        )
        print(
            "[OK] Installed mutual_follows_of(person_id) and "
            "common_follows_of(first_id, second_id)"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build disk-backed source and destination indexes for the full "
            "follow graph."
        )
    )
    parser.add_argument(
        "--direction",
        choices=("src", "dst", "both"),
        default="both",
        help="index outgoing, incoming, or both adjacency directions",
    )
    parser.add_argument(
        "--partitions",
        type=int,
        default=256,
        help="number of hash partitions; keep this unchanged after the first build",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="delete and recreate the selected follow-index directories",
    )
    args = parser.parse_args()

    if args.partitions < 16 or args.partitions > 1024:
        parser.error("--partitions must be between 16 and 1024")

    directions = ("src", "dst") if args.direction == "both" else (args.direction,)

    with connect() as connection:
        connection.execute(
            f"SET partitioned_write_max_open_files = {args.partitions}"
        )
        for direction in directions:
            _build_index(
                connection,
                direction=direction,
                partitions=args.partitions,
                rebuild=args.rebuild,
            )
        _install_query_macros(connection, args.partitions)

    print(
        "\nBuild complete. Keep the same --partitions value for future rebuilds."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
