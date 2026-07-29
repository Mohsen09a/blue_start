# Paper-Compatible Pair Co-occurrence

## Goal

The analysis counts how frequently each pair of users appears together in
Starter Packs.

For a pack with `n` unique members, the number of pair occurrences is:

```text
n * (n - 1) / 2
```

## Matching the paper

The official implementation treats each hyperedge as a set. The local
pipeline therefore removes duplicate `(pack_id, member_id)` records first.

The paper-compatible parameters are:

```text
Maximum exactly processed pack size: 4,069
Sample size for larger packs:         1,000 pairs
```

Packs at or below the threshold are processed exactly. For a pack above the
threshold, the implementation samples unique pairs and gives each sampled
pair this weight:

```text
all possible pairs / sampled pairs
```

Weighted counts are combined across packs and rounded using Python-compatible
round-to-even behavior.

After deduplication, the largest local pack has exactly 4,069 unique members.
Consequently, no sampling was needed and the completed local result is exact.

## Disk-safe implementation

Generating all pairs creates 415,832,406 occurrence rows. They are not held in
RAM.

The pipeline:

1. generates pair occurrences with DuckDB;
2. assigns each pair to one of 256 hash buckets;
3. writes the buckets as compressed temporary Parquet files;
4. aggregates one bucket at a time;
5. combines the small bucket distributions;
6. writes the final result;
7. removes the temporary pair files.

This keeps the largest in-memory aggregation limited to one bucket.

## Results

```text
Runtime:                       58.40 seconds
Nonempty packs:                 365,611
Unique memberships:         12,703,609
Exact pair occurrences:    415,832,406
Distinct user pairs:       245,754,884
Maximum co-occurrence:          32,696
Distribution rows:               4,141
Temporary files:                   256
Temporary storage:               2.60 GB
Sampled packs:                       0
```

The full local distribution has zero differing rows when compared with the
official imported distribution.

## Run command

```cmd
cd /d E:\blue-start-duckdb
scripts\run_pair_cooccurrence_paper.cmd
```

Useful options:

```cmd
scripts\run_pair_cooccurrence_paper.cmd --rebuild-pairs
scripts\run_pair_cooccurrence_paper.cmd --keep-pair-rows
```

The normal command removes temporary pair rows after success. Use
`--keep-pair-rows` only when the intermediate 2.60 GB partitioned dataset is
needed for inspection.

## Outputs

```text
E:\blue-start-duckdb\outputs\parquet\pair_cooccurrence_paper_compatible.parquet
E:\blue-start-duckdb\outputs\summaries\pair_cooccurrence_paper_compatible.json
E:\blue-start-duckdb\work\blue_start.duckdb
```

DuckDB table:

```text
results.pair_cooccurrence_paper_compatible
```
