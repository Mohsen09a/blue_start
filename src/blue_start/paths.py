from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    canonical_name: str
    required: bool = True


DATASET_SPECS = (
    DatasetSpec("nodes_csv", "deidentified_nodes.csv.gz"),
    DatasetSpec("nodes_jsonl", "deidentified_nodes.jsonl.gz"),
    DatasetSpec("starterpacks_jsonl", "deidentified_starterpacks.jsonl.gz"),
    DatasetSpec("starterpack_edgelist", "deidentified_starterpack_edgelist.csv.gz"),
    DatasetSpec("starterpack_hif", "deidentified_starterpack_hif.json.gz"),
    DatasetSpec("follows_csv", "deidentified_follows_edgelist.csv.gz"),
    DatasetSpec("follows_parquet", "deidentified_follows_edgelist.parquet"),
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return project_root() / "data"


def resolve_dataset(spec: DatasetSpec, root: Path | None = None) -> Path | None:
    root = root or data_dir()
    canonical = root / spec.canonical_name
    if canonical.exists():
        return canonical

    # Browsers may insert "(1)" before either the first or the last suffix.
    # Normalize it away instead of relying on a single filename pattern.
    duplicates = sorted(
        path
        for path in root.iterdir()
        if path.is_file()
        and re.sub(r"\s*\(\d+\)", "", path.name) == spec.canonical_name
    )
    return duplicates[0] if duplicates else None


def resolved_datasets(root: Path | None = None) -> dict[str, Path | None]:
    return {spec.key: resolve_dataset(spec, root) for spec in DATASET_SPECS}
