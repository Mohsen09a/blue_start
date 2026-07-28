from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .paths import project_root


_SIZE_RE = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+)?(?:KB|MB|GB|TB)$", re.IGNORECASE)


@dataclass(frozen=True)
class DuckDBSettings:
    database: Path
    temp_directory: Path
    parquet_outputs: Path
    summary_outputs: Path
    figure_outputs: Path
    memory_limit: str
    threads: int
    max_temp_directory_size: str
    preserve_insertion_order: bool
    enable_progress_bar: bool


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _checked_size(value: str, field: str) -> str:
    if not _SIZE_RE.fullmatch(value):
        raise ValueError(f"{field} must look like '18GB', got {value!r}")
    return value.upper()


def load_settings(config_path: Path | None = None) -> DuckDBSettings:
    root = project_root()
    config_path = config_path or root / "config" / "default.toml"
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)

    duck = config["duckdb"]
    paths = config["paths"]

    memory_limit = os.getenv("BLUE_START_MEMORY_LIMIT", str(duck["memory_limit"]))
    threads = int(os.getenv("BLUE_START_THREADS", str(duck["threads"])))
    max_temp = os.getenv(
        "BLUE_START_MAX_TEMP_DIRECTORY_SIZE",
        str(duck["max_temp_directory_size"]),
    )
    if threads < 1:
        raise ValueError("threads must be positive")

    return DuckDBSettings(
        database=_resolve_path(root, paths["database"]),
        temp_directory=_resolve_path(root, paths["temp_directory"]),
        parquet_outputs=_resolve_path(root, paths["parquet_outputs"]),
        summary_outputs=_resolve_path(root, paths["summary_outputs"]),
        figure_outputs=_resolve_path(root, paths["figure_outputs"]),
        memory_limit=_checked_size(memory_limit, "memory_limit"),
        threads=threads,
        max_temp_directory_size=_checked_size(max_temp, "max_temp_directory_size"),
        preserve_insertion_order=bool(duck["preserve_insertion_order"]),
        enable_progress_bar=bool(duck["enable_progress_bar"]),
    )
