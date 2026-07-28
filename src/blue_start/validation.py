from __future__ import annotations

import csv
import gzip
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .paths import resolved_datasets


@dataclass(frozen=True)
class CheckResult:
    dataset: str
    ok: bool
    detail: str


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _read_gzip_lines(path: Path, limit: int) -> list[str]:
    lines: list[str] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for _, line in zip(range(limit), handle):
            lines.append(line.rstrip("\r\n"))
    return lines


def _check_nodes_csv(path: Path, limit: int) -> str:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"id", "date-created", "active", "status"}
        if set(reader.fieldnames or ()) != expected:
            raise ValueError(f"unexpected columns: {reader.fieldnames}")
        count = 0
        for row in reader:
            int(row["id"])
            if row["date-created"] and not _valid_date(row["date-created"]):
                raise ValueError("invalid date-created")
            count += 1
            if count >= limit:
                break
    return f"{count} sampled rows; columns are valid"


def _check_nodes_jsonl(path: Path, limit: int) -> str:
    rows = [json.loads(line) for line in _read_gzip_lines(path, limit)]
    for row in rows:
        if not isinstance(row.get("id"), int):
            raise ValueError("node id must be an integer")
        created = row.get("date-created")
        if created and not _valid_date(created):
            raise ValueError("invalid date-created")
    return f"{len(rows)} sampled records are valid"


def _check_starterpacks(path: Path, limit: int) -> str:
    rows = [json.loads(line) for line in _read_gzip_lines(path, limit)]
    for row in rows:
        required = {"pack-id", "date-created", "creator-id", "members"}
        if not required.issubset(row):
            raise ValueError(f"missing keys: {sorted(required - set(row))}")
        if not _valid_date(row["date-created"]):
            raise ValueError("invalid pack date-created")
        for member in row["members"]:
            int(member["id"])
            if not _valid_date(member["date-added"]):
                raise ValueError("invalid member date-added")
    return f"{len(rows)} sampled packs are valid"


def _check_starterpack_edgelist(path: Path, limit: int) -> str:
    rows = _read_gzip_lines(path, limit)
    for row in rows:
        members = row.split(",")
        if not members or any(not value.isdigit() for value in members):
            raise ValueError("non-integer member in hyperedge")
    return f"{len(rows)} sampled hyperedges are valid"


def _check_follows(path: Path, limit: int) -> str:
    rows = _read_gzip_lines(path, limit)
    for row in rows:
        values = row.split(",")
        if len(values) != 3:
            raise ValueError("follow row must contain 3 fields")
        int(values[0])
        int(values[1])
        if not _valid_date(values[2]):
            raise ValueError("invalid date_followed")
    return f"{len(rows)} sampled edges are valid"


CHECKERS: dict[str, Callable[[Path, int], str]] = {
    "nodes_csv": _check_nodes_csv,
    "nodes_jsonl": _check_nodes_jsonl,
    "starterpacks_jsonl": _check_starterpacks,
    "starterpack_edgelist": _check_starterpack_edgelist,
    "follows_csv": _check_follows,
}


def validate_datasets(sample_size: int = 100) -> list[CheckResult]:
    if sample_size < 1:
        raise ValueError("sample_size must be positive")

    results: list[CheckResult] = []
    for key, path in resolved_datasets().items():
        if path is None:
            results.append(CheckResult(key, False, "file not found"))
            continue
        checker = CHECKERS.get(key)
        if checker is None:
            results.append(CheckResult(key, True, "present; deep validation not run"))
            continue
        try:
            detail = checker(path, sample_size)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            results.append(CheckResult(key, False, str(exc)))
        else:
            results.append(CheckResult(key, True, detail))
    return results


def inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, path in resolved_datasets().items():
        rows.append(
            {
                "dataset": key,
                "path": str(path) if path else None,
                "bytes": path.stat().st_size if path else None,
            }
        )
    return rows

