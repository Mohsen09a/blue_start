# DuckDB and Resource Guide for a 32 GB Workstation

## Architecture

The 2,416,311,437-row follow dataset remains an external Parquet relation.
DuckDB reads required columns and spills bounded intermediates to disk.
Smaller node and Starter Pack relations are materialized in
`work/blue_start.duckdb`.

Large graph algorithms use additional bounded representations:

- partitioned Parquet follow indexes;
- disk-backed CSR arrays for exact SCC;
- partitioned weighted clique-projection files;
- a 32-bit native C/igraph Leiden backend;
- compact bidirectional CSR plus native batches for exact s-line counts.

## Default Limits

`config/default.toml` uses:

- DuckDB memory limit: `18GB`;
- worker threads: `8`;
- spill directory: `work/duckdb_tmp`;
- maximum spill size: `250GB`;
- insertion-order preservation disabled.

Temporary overrides:

```cmd
set BLUE_START_MEMORY_LIMIT=14GB
set BLUE_START_THREADS=6
python -m blue_start.cli doctor
```

## Completed Large Workloads

| Workload | Result |
|---|---|
| Follow aggregation | All 2.416 billion edges |
| Follow indexes | 256 source + 256 destination partitions |
| Exact WCC | Full graph, official exact match |
| Exact SCC | Full graph, official exact match |
| Pair co-occurrence | Exact complete local distribution |
| Clique projection | 245,754,884 weighted edges |
| Full s-line | `s=1..345`, official exact match |
| Independent Leiden | 1,997,488 nodes, 245,669,033 edges |

## Safety Rules

- Do not load the complete follow graph with pandas or NetworkX.
- Keep `work/` on a drive with substantial free space.
- Run only one DuckDB writer at a time.
- Preserve completed checkpoint directories.
- Do not use `starterpack-leiden --python-backend` on a 32 GB machine.
- Use `--maximum-new-batches` or the SCC bucket limit for bounded probes.
- Keep raw data, databases, indexes, native binaries, and checkpoints out of
  Git.

## Checkpointed Commands

Exact SCC:

```cmd
python -m blue_start.cli follow-scc
```

Exact unrestricted s-line:

```cmd
python -m blue_start.cli s-line-full
python -m blue_start.cli s-line-full --maximum-new-batches 1
```

Safe completed Leiden import:

```cmd
python -m blue_start.cli starterpack-leiden
```

## Cache Behavior

Completed outputs are reused when possible. Rebuild flags intentionally remove
only the command's validated work directory:

```cmd
python -m blue_start.cli following --force
python -m blue_start.cli follow-scc --rebuild
python -m blue_start.cli s-line-full --rebuild
```

Deleting `work/blue_start.duckdb` removes database tables but not every
partitioned external index or algorithm checkpoint.

## GitHub Packaging

Portable summaries, figures, source code, tests, and reasonably sized Parquet
outputs are committed. The following remain local:

- raw datasets;
- DuckDB databases and spill files;
- full per-node follow degree/time tables over GitHub's size limit;
- the 20.28 GiB follow indexes;
- clique-projection partitions;
- SCC and s-line checkpoints;
- native build products.

