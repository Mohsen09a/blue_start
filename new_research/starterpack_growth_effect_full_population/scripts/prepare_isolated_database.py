from __future__ import annotations

import argparse
import shutil
from pathlib import Path


STUDY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE = PROJECT_ROOT / "work" / "blue_start.duckdb"
DESTINATION = STUDY_ROOT / "work" / "full_population.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the study's isolated DuckDB copy from the prepared project database."
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not SOURCE.exists():
        raise FileNotFoundError(
            f"Prepared project database not found: {SOURCE}. Run the main database preparation first."
        )
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    if DESTINATION.exists() and not args.force:
        print(f"[OK] Isolated database already exists: {DESTINATION}")
        return 0
    temporary = DESTINATION.with_suffix(".duckdb.tmp")
    temporary.unlink(missing_ok=True)
    shutil.copy2(SOURCE, temporary)
    temporary.replace(DESTINATION)
    print(f"[OK] Copied {SOURCE} to {DESTINATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
