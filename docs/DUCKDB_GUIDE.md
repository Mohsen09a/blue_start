# DuckDB Guide for a 32 GB Workstation

## Architecture

The follow dataset contains 2,416,311,437 rows and is not copied into the
database. The `follows` relation is an external view over the Parquet file, so
DuckDB reads only the required columns and can push filters into the scan.

The smaller datasets are materialized once:

- `nodes`: 39,650,447 rows;
- `starterpacks`: 365,842 rows;
- `starterpack_memberships`: 12,812,086 rows.

Computed relations are stored in the `results` schema. Portable result files
are written to `outputs/parquet/`.

## Resource Limits

The defaults in `config/default.toml` are:

- DuckDB memory limit: `18GB`;
- worker threads: `8`;
- spill directory: `work/duckdb_tmp`;
- maximum spill size: `250GB`;
- `preserve_insertion_order=false`.

DuckDB reports decimal GB values as binary GiB values. Therefore, an `18GB`
limit appears as approximately `16.7 GiB`; this is expected.

Temporary overrides:

```cmd
set BLUE_START_MEMORY_LIMIT=14GB
set BLUE_START_THREADS=6
python -m blue_start.cli doctor
```

## Observed Runtime on the Development Machine

- preparing nodes and starter packs: approximately 8 seconds;
- exact hypergraph connected components: approximately 15 seconds;
- full follow degree and volume analysis: approximately 170 seconds;
- temporal standard deviations and timestamp validation: approximately 247 seconds;
- Kendall tau for the top one million nodes: approximately 15 seconds.

These measurements are not guarantees. Runtime depends on disk performance,
the operating-system cache, and other system workloads.

## Reproduction Levels

### Exact Local Computations

- node statistics;
- full follow degree and temporal distributions;
- standard deviations of follow dates;
- impossible timestamp validation;
- basic starter-pack statistics;
- exact starter-pack hypergraph components;
- Kendall tau rank comparison;
- exact pair co-occurrence for an explicitly bounded pack-size range.

### Filtered Local Computations

- s-line statistics after removing hyper-hub members;
- pair co-occurrence with an explicit `--max-pack-size`.

The filter is included in every output filename to prevent filtered results
from being confused with unfiltered results.

### Official Reference Results

The following full computations are not practical on a 32 GB workstation:

- strongly and weakly connected components of the 2.4-billion-edge graph;
- the full clique projection and Leiden clustering;
- unfiltered s-line construction;
- loading the full graph into igraph or graph-tool.

The `reference-import` command imports the authors' official outputs with a
`reference_` prefix. These values are never reported as locally recomputed
results.

## Expensive Hypergraph Commands

```cmd
python -m blue_start.cli pair-cooccurrence --max-pack-size 50
python -m blue_start.cli s-line --s-max 5 --max-member-degree 5000
python -m blue_start.cli starterpack-kcore
```

The k-core implementation is exact, but its Python adjacency structures can
consume several gigabytes of memory. Run it separately from VS Code, browsers,
and other memory-intensive applications.

## Cache Behavior

The `following` command reuses existing degree and volume tables. Force a
recomputation with:

```cmd
python -m blue_start.cli following --force
```

Deleting `work/blue_start.duckdb` removes all cached computations.

