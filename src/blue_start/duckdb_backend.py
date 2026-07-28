from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .settings import DuckDBSettings, load_settings


def sql_path(path: Path) -> str:
    """Return a safely quoted SQL string literal for a local path."""
    return "'" + path.resolve().as_posix().replace("'", "''") + "'"


def _load_duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB is not installed. Run: python -m pip install -e ."
        ) from exc
    return duckdb


@contextmanager
def connect(
    settings: DuckDBSettings | None = None,
    *,
    read_only: bool = False,
) -> Iterator[object]:
    settings = settings or load_settings()
    settings.database.parent.mkdir(parents=True, exist_ok=True)
    settings.temp_directory.mkdir(parents=True, exist_ok=True)
    settings.parquet_outputs.mkdir(parents=True, exist_ok=True)
    settings.summary_outputs.mkdir(parents=True, exist_ok=True)
    settings.figure_outputs.mkdir(parents=True, exist_ok=True)

    duckdb = _load_duckdb()
    connection = duckdb.connect(str(settings.database), read_only=read_only)
    try:
        connection.execute(f"SET memory_limit = '{settings.memory_limit}'")
        connection.execute(f"SET threads = {settings.threads}")
        connection.execute(
            f"SET temp_directory = {sql_path(settings.temp_directory)}"
        )
        connection.execute(
            f"SET max_temp_directory_size = '{settings.max_temp_directory_size}'"
        )
        connection.execute(
            "SET preserve_insertion_order = "
            + ("true" if settings.preserve_insertion_order else "false")
        )
        if settings.enable_progress_bar:
            connection.execute("PRAGMA enable_progress_bar")
        else:
            connection.execute("PRAGMA disable_progress_bar")
        yield connection
    finally:
        connection.close()


def export_query(
    connection: object,
    query: str,
    output_path: Path,
    *,
    compression: str = "zstd",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(
        f"""
        COPY ({query})
        TO {sql_path(output_path)}
        (FORMAT parquet, COMPRESSION {compression}, ROW_GROUP_SIZE 250000)
        """
    )
