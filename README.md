# Bluesky Network Analysis

This project reproduces the computational analyses from **A Blue Start: A
large-scale pairwise and higher-order social network dataset** on a workstation
with 32 GB of RAM.

The released data contains a directed follow network with 2,416,311,437 edges
and a Starter Pack hypergraph. DuckDB, partitioned Parquet, compact arrays, and
native bounded-memory kernels are used so the largest relations are never
loaded into Python objects.

## Current Status

All major paper analyses have been implemented:

- full node, follow, and Starter Pack statistics;
- exact full-network weakly and strongly connected components;
- exact hypergraph components and k-core;
- exact paper-compatible pair co-occurrence;
- full disk-backed weighted clique projection;
- exact unrestricted s-line counts for `s=1..345`;
- full independent Leiden community detection;
- observed and configuration-model community entropy;
- Kendall tau rank comparisons;
- research figures and fast follow-query indexes.

The unrestricted s-line output matches all 345 official rows exactly. The
independent Leiden run is complete, but uses a memory-safe C/igraph backend
instead of the paper's high-memory Python `leidenalg` backend; see
[the Leiden documentation](docs/INDEPENDENT_LEIDEN.md).

## Installation on Windows

```cmd
cd /d E:\blue-start-duckdb
C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m pip install -e ".[analysis,graphs,dev]"
```

Native full s-line rebuilding requires GCC with OpenMP support. The tested
compiler path is `C:\mingw64\bin\gcc.exe`. Completed portable result files are
already included in this repository.

## Prepare and Validate

Place the released raw datasets under `data/`, then run:

```cmd
python -m blue_start.cli inventory
python -m blue_start.cli validate
python -m blue_start.cli doctor
python -m blue_start.cli prepare
```

## Main Workflows

Core full-dataset workflow:

```cmd
scripts\run_full.cmd
```

Advanced paper analyses:

```cmd
scripts\run_remaining_paper_tasks.cmd
```

Important individual commands:

```cmd
python -m blue_start.cli follow-wcc
python -m blue_start.cli follow-scc
python -m blue_start.cli starterpack-kcore
python -m blue_start.cli pair-cooccurrence-paper
python -m blue_start.cli clique-projection
python -m blue_start.cli starterpack-leiden
python -m blue_start.cli edge-entropy --label-source independent
python -m blue_start.cli configuration-model --label-source independent
python -m blue_start.cli s-line-full
python -m blue_start.cli plot all --follow-profile full
```

Build the disk-partitioned follow indexes:

```cmd
scripts\build_follow_indexes.cmd
scripts\query_indexed_follows.cmd
```

## Project Structure

```text
config/               DuckDB memory, thread, and spill settings
data/                 Raw released datasets; excluded from Git
docs/                 Technical and reproduction documentation
native/               Bounded-memory C kernels
outputs/parquet/       Portable result tables
outputs/summaries/     JSON execution summaries
outputs/figures/       PNG and PDF figures
paper/                 Reference paper
reference/             Upstream repository instructions and reference outputs
scripts/               Reproduction and query commands
src/blue_start/        Main Python implementation
tests/                 Automated tests
work/                  Rebuildable databases, indexes, checkpoints, and binaries
```

Large generated relations, raw data, the DuckDB database, follow indexes, and
native checkpoints are intentionally excluded from Git.

## Documentation

- [Implementation status](docs/IMPLEMENTATION_STATUS.md)
- [Advanced analyses](docs/ADVANCED_ANALYSES.md)
- [Exact SCC](docs/EXACT_SCC.md)
- [Exact unrestricted s-line](docs/FULL_SLINE.md)
- [Independent Leiden](docs/INDEPENDENT_LEIDEN.md)
- [Pair co-occurrence](docs/PAIR_COOCCURRENCE.md)
- [Clique projection](docs/CLIQUE_PROJECTION.md)
- [Follow indexes](docs/FOLLOW_INDEX_GUIDE.md)
- [Complete work summary](docs/PROJECT_WORK_SUMMARY.md)
