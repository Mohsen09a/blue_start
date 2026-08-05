# Full-Population Starter Pack Growth Study

This is an isolated rerun of the Starter Pack growth study using every eligible
treated user instead of the original deterministic sample of 100,000 users.
The original `new_research/starterpack_growth_effect` directory is not modified.

## Isolation and memory safety

- The study has its own copy of the prepared DuckDB database in `work/`.
- DuckDB is limited to 14 GB RAM and may spill at most 100 GB to the local
  `work/duckdb_tmp` directory.
- All 1,084,011 eligible treated users are included.
- Up to eight deterministic risk-set control candidates are generated per
  treated user before date/exposure eligibility filtering.
- The complete 2.416-billion-edge relation stays in external partitioned
  Parquet files and is never loaded into Python.
- Propensity matching uses compact NumPy arrays, a one-pass stratum sort, and
  inserts final matches into DuckDB in batches of 20,000.

## Run

```cmd
cd /d E:\final_proj
new_research\starterpack_growth_effect_full_population\scripts\prepare_isolated_database.cmd
new_research\starterpack_growth_effect_full_population\scripts\run_full_study.cmd
```

All outputs are written below this directory in `outputs/`. The isolated
database and rebuildable spill files are kept in `work/`.

## Completed result

- Eligible treated users analyzed: 1,084,011 (no treated-user sample)
- Matched treated-control pairs: 910,685
- Unique matched controls: 378,615
- Raw 90-day follower difference: +230.96
- 99th-percentile-capped 90-day difference: +171.77
- Maximum absolute post-match SMD: 0.0227

The monolithic final reciprocity join reached the safe 14 GB limit. It was
replaced by `scripts/recover_network_quality.py`, which computes the exact same
metrics in 256 resumable hash partitions with an 8 GB cap. The completed
summary is in `outputs/summaries/starterpack_growth_effect_full_population.json`.
The CMD runner catches that bounded-memory condition and automatically invokes
the partitioned recovery and finalization scripts; a completed run is reused.

## Repository package

The checked-in package includes all source code and final outputs. The local
2 GB isolated database, temporary DuckDB spill files, logs, rendered QA pages,
and 256 rebuildable JSON checkpoints are intentionally excluded. Use
`prepare_isolated_database.cmd` to create the private database before a fresh
rerun. The already completed outputs can be read without recreating it.
