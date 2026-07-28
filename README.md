# Bluesky Network Analysis

This project reproduces and extends the analyses from
**A Blue Start: A large-scale pairwise and higher-order social network dataset**
on a workstation with 32 GB of RAM.

The dataset contains a directed follow network with 2.416 billion edges and a
higher-order starter-pack network. DuckDB is used for out-of-core processing,
so the complete follow dataset is never loaded into RAM.

## Quick Start on Windows CMD

Install the project once:

```cmd
cd /d E:\blue-start-duckdb
C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m pip install -e ".[analysis]"
```

Check the environment and prepare the database:

```cmd
python -m blue_start.cli doctor
python -m blue_start.cli prepare
```

Run the core analyses:

```cmd
python -m blue_start.cli nodes
python -m blue_start.cli starterpacks
python -m blue_start.cli starterpack-components
```

Run a smoke test on one million follow edges:

```cmd
python -m blue_start.cli following --row-limit 1000000 --time-std --impossible-timestamps
```

Process the complete 2.416-billion-edge follow network:

```cmd
python -m blue_start.cli following --time-std --impossible-timestamps
python -m blue_start.cli kendall-tau --follow-profile full --top-k 1000000
```

Import the official HPC-only reference results and render the figures:

```cmd
python -m blue_start.cli reference-import
python -m blue_start.cli plot all --follow-profile full
```

Alternatively, run a complete workflow:

```cmd
scripts\run_smoke.cmd
scripts\run_full.cmd
```

## Lightweight Validation Without Installing the Package

```cmd
cd /d E:\blue-start-duckdb
set PYTHONPATH=E:\blue-start-duckdb\src
python -m blue_start.cli inventory
python -m blue_start.cli validate
```

## Project Structure

```text
config/               DuckDB memory, thread, and spill settings
data/                 Raw datasets; excluded from Git
docs/                 Research plan and technical documentation
paper/                Reference paper
outputs/parquet/       Generated result tables
outputs/summaries/     JSON summaries
outputs/figures/       PNG and PDF figures
reference/             Official upstream repository and result artifacts
src/blue_start/        Main implementation
tests/                 Automated tests
work/                  Local DuckDB database and temporary spill files
```

See the [DuckDB guide](docs/DUCKDB_GUIDE.md) for resource and reproducibility
details, and the [project plan](docs/PROJECT_PLAN.md) for the proposed research
phases.

For an exact breakdown of completed, partially executed, and HPC-only work, see
the [implementation status](docs/IMPLEMENTATION_STATUS.md).
