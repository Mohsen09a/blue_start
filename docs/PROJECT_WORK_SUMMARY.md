# Blue Start Project: Work Completed

## 1. Project goal

This project reproduces and extends parts of the paper:

> **A Blue Start: A Large-Scale Pairwise and Higher-Order Social Network
> Dataset**

The data describes two related Bluesky networks:

- a directed follow network;
- a higher-order Starter Pack network.

The main challenge is the size of the follow graph. It contains more than
2.4 billion edges, while the computer has 32 GB of RAM.

The solution was to use DuckDB for disk-backed and streaming processing. The
complete graph is never loaded into RAM as one graph object.

## 2. Dataset preparation

The project detects the downloaded files even when Windows has added names
such as `(1)` to them.

The input data was validated by checking file availability, required columns,
data types, and sample records.

The full dataset contains:

| Data | Count |
|---|---:|
| Nodes | 39,650,447 |
| Follow edges | 2,416,311,437 |
| Starter Packs | 365,842 |
| Starter Pack memberships | 12,812,086 |

The validation command is:

```cmd
cd /d E:\blue-start-duckdb
python -m blue_start.cli validate
```

## 3. DuckDB database

The database is stored at:

```text
E:\blue-start-duckdb\work\blue_start.duckdb
```

The following datasets were materialized as DuckDB tables:

- `nodes`
- `starterpacks`
- `starterpack_memberships`

The large follow dataset remains an external Parquet view:

```text
follows(src, dst, date_followed)
```

This avoids copying all 2.4 billion edges into the database and allows DuckDB
to scan the Parquet file directly.

The DuckDB configuration uses:

- 18 GB memory limit;
- 8 processing threads;
- disk spilling for large operations;
- a temporary-disk limit of 250 GB;
- disabled insertion-order preservation to reduce memory usage.

## 4. Full analyses completed locally

The following calculations were executed against the complete local dataset.

### Node analysis

- Total nodes: 39,650,447
- Active nodes: 36,687,381
- Extant nodes: 37,479,031
- Nodes with unknown activity: 791,650
- Account-creation volume over time
- Account status counts

### Follow-network analysis

- Full in-degree for every node
- Full out-degree for every node
- Degree distributions
- Daily follow volume
- Standard deviation of incoming follow dates
- Standard deviation of outgoing follow dates
- Detection of impossible follow timestamps

Important results:

| Result | Value |
|---|---:|
| Follow edges | 2,416,311,437 |
| Nodes with at least one follow edge | 36,447,725 |
| Maximum in-degree | 28,062,787 |
| Maximum out-degree | 844,408 |
| Impossible timestamps | 147,655 |

The saved full-follow run took approximately 247 seconds for its recorded
workload.

### Starter Pack analysis

- Starter Pack degree distribution
- Pack-size distribution
- Pack creation over time
- Number of packs created per user
- Creator-account age at pack creation
- Exact connected components of the Starter Pack hypergraph

Important results:

| Result | Value |
|---|---:|
| Starter Packs | 365,842 |
| Unique Starter Pack members | 2,003,536 |
| Maximum hypergraph degree | 175,320 |
| Maximum pack size | 4,661 |
| Connected components | 409 |
| Largest component | 1,997,488 nodes |
| Negative creator ages | 9 |

The exact hypergraph component calculation completed in approximately 15
seconds.

### Ranking comparison

Kendall rank comparisons were calculated between follow-network degree and
Starter Pack degree.

The comparison used the top one million nodes from each ranking and found
536,072 nodes common to both top-one-million sets.

## 5. Advanced graph and hypergraph analyses

Seven additional paper analyses were implemented with compact arrays,
partitioned Parquet, and DuckDB, then executed locally.

### Exact follow-network WCC

All 2,416,311,437 edges were streamed through a Numba union-find structure.

```text
Runtime:                    105.53 seconds
Follow-network nodes:       36,447,725
Weak components:                 6,735
Largest component:          36,433,172
```

The complete local WCC distribution is identical to the official reference
distribution.

### Exact follow-network SCC

Forward and reverse CSR arrays were constructed from the existing source and
destination follow indexes. The 2.4-billion-edge adjacency arrays remain as
memory-mapped `uint32` files, while iterative Numba Kosaraju traversals use
compact working arrays.

```text
Runtime:                    3,060.76 seconds
Follow-network nodes:      36,447,725
Strong components:         15,883,064
Largest component:         20,495,220
Forward DFS:                  460.87 seconds
Reverse DFS:                   23.99 seconds
```

The complete local SCC distribution is identical to the official reference
distribution, with zero differing rows.

### Exact hypergraph k-core

Starter Pack memberships were deduplicated to match XGI's set semantics and
stored as compact CSR incidence arrays.

```text
Runtime:                      5.16 seconds
Hypergraph nodes:            2,003,536
Deduplicated incidences:    12,703,609
Maximum core number:            34,297
Nodes with core >= 1,000:           772
```

The maximum core number and the important `core >= 1,000` result match the
official output. Small distribution differences are expected because the
local dataset is newer than the paper snapshot.

### Independently recomputed edge entropy

The official Leiden node labels were joined to deduplicated Starter Pack
memberships, but entropy itself was independently calculated locally.

```text
Fully labeled packs:          365,157
Communities:                      503
Mean normalized entropy:     0.160416
```

This reproduces the paper's reported mean of approximately `0.160`.

### Configuration-model randomization

The XGI `random_edge_shuffle` behavior was reimplemented with packed arrays.
The process preserves every node degree and every edge size.

```text
Random shuffle attempts:     3,651,570
Swaps per edge:                     10
Mean randomized entropy:      0.575723
```

This reproduces the paper's reported randomized mean of approximately `0.576`.

The unrestricted full s-line calculation has now been recomputed locally for
all thresholds from 1 through 345. Its 345 output rows exactly match the
official result. Full independent Leiden community detection has also been
completed with the memory-safe 32-bit C/igraph backend described in
`docs/INDEPENDENT_LEIDEN.md`.

The official repository reports memory requirements of approximately:

- 310 GB with graph-tool;
- 460 GB with igraph.

The official published output files remain imported with a `reference_`
prefix for independent validation. They are clearly separated from locally
computed results.

Imported official results include:

| Result | Official value |
|---|---:|
| Largest strongly connected component | 20,495,220, locally verified |
| Largest weakly connected component | 36,433,172, locally verified |
| Nodes with k-core at least 1,000 | 772, locally verified |

### Full paper-compatible pair co-occurrence

Starter Pack memberships were deduplicated to match XGI set semantics. All
packs then had at most 4,069 unique members, so the paper-compatible run was
exact for the complete local hypergraph and did not need its large-pack
sampling fallback.

```text
Runtime:                         58.40 seconds
Exact pair occurrence rows:    415,832,406
Distinct user pairs:           245,754,884
Maximum co-occurrence:              32,696
Temporary pair storage:            2.60 GB
```

The complete local distribution is identical to the official imported
pair-co-occurrence distribution. The temporary pair partitions were removed
after successful aggregation.

### Full weighted clique projection

The 415,832,406 pair occurrences were hash-partitioned, aggregated into unique
weighted edges, and materialized as 256 compressed Parquet files. The complete
projection remains on disk; it is exposed through a DuckDB view and is never
loaded into RAM as one graph object.

```text
Runtime:                         97.84 seconds
Projected nodes:                 2,003,530
Weighted projected edges:      245,754,884
Maximum projected degree:          912,507
Maximum node strength:            5,269,118
Maximum edge weight:                 32,696
Projection storage:              1.79 GB
```

The projection edge count exactly matches the independently computed
pair-co-occurrence distribution. The 2.60 GB pair-occurrence intermediate
files were removed after successful materialization.

The unrestricted full s-line calculation was completed without filtering or
sampling:

```text
Runtime:                           39.83 seconds
Thresholds:                           1-345
s=1 active packs:                   365,228
s=1 distinct pack pairs:     19,559,507,901
Official differing rows:                  0
Checkpoint storage:                 189 MB
```

The implementation counts overlap frequencies and maximum partner overlaps
without materializing the 19.6-billion-edge line graph. See
`docs/FULL_SLINE.md`.

## 6. Figures and outputs

The project generates:

- node-creation and account-status figures;
- follow-volume and follow-degree figures;
- Starter Pack distribution figures;
- temporal analysis figures;
- mesoscale network figures;
- weighted clique-projection degree and edge-weight figures;
- Kendall-ranking comparison figures.

Generated files are organized under:

```text
outputs/parquet/    Computed tables
outputs/summaries/  JSON summaries
outputs/figures/    PNG and PDF figures
```

The figures were rendered and visually inspected.

## 7. Query examples

Read-only SQL examples and a timed query runner were added:

```text
docs/QUERY_EXAMPLES.sql
scripts/query_examples.py
scripts/run_query_examples.cmd
```

Before follow indexing, example person-level query times were approximately:

| Query | Time |
|---|---:|
| Cached degree lookup | 0.02-0.8 seconds |
| People followed by one person | 3-5.6 seconds |
| People following one person | 11.6 seconds |
| Starter Pack lookup | 0.13 seconds |

The sample person ID is `21486540`. This deidentified account has:

- out-degree: 844,408;
- in-degree: 69,098.

The dataset contains numeric deidentified IDs, not real handles or display
names.

## 8. Follow adjacency indexes

The original follow relation is an external Parquet view, so DuckDB cannot
attach a normal ART index directly to it.

To accelerate interactive follow queries, two disk-backed adjacency indexes
were created.

### Outgoing index

Each edge was assigned to a bucket using:

```sql
hash(src) % 256
```

The result was saved as 256 Zstandard-compressed Parquet partitions:

```text
work/follow_indexes/by_src
```

Build result:

- build time: 122.09 seconds;
- output size: 8.95 GiB;
- files: 256.

### Incoming index

Each edge was assigned to a bucket using:

```sql
hash(dst) % 256
```

The result was saved at:

```text
work/follow_indexes/by_dst
```

Build result:

- build time: 206.73 seconds;
- output size: 11.33 GiB;
- files: 256.

Together, the two indexes use approximately 20.28 GiB.

For a person-level query, DuckDB now reads one relevant partition instead of
scanning the complete 12.56 GB source file.

## 9. Indexed query functions

The index builder installed simple DuckDB macros.

People followed by a person:

```sql
SELECT *
FROM follows_of(21486540)
LIMIT 20;
```

People following a person:

```sql
SELECT *
FROM followers_of(21486540)
LIMIT 20;
```

Check one directed follow edge:

```sql
SELECT is_following(21486540, 3528659);
```

Find mutual follows:

```sql
SELECT *
FROM mutual_follows_of(21486540)
LIMIT 20;
```

Find accounts followed by two people:

```sql
SELECT *
FROM common_follows_of(21486540, 3528659)
LIMIT 20;
```

## 10. Indexed query performance

The completed indexes were tested against the full dataset.

| Indexed query | Measured time |
|---|---:|
| Outgoing follows, ordered by date | 1.1123 seconds |
| Incoming followers, ordered by date | 0.0532 seconds |
| One edge-existence check | 0.0260 seconds |
| Exact incoming and outgoing counts | 0.0582 seconds |
| Mutual follows | 0.0750 seconds |
| Common follows | 0.0550 seconds |

The outgoing test account has 844,408 outgoing edges, so sorting its results
by date is more expensive than the other person-level queries.

Run the indexed benchmark with:

```cmd
cd /d E:\blue-start-duckdb
scripts\query_indexed_follows.cmd --limit 20
```

## 11. Main commands

Check the environment:

```cmd
python -m blue_start.cli doctor
```

Prepare the database:

```cmd
python -m blue_start.cli prepare
```

Run the complete DuckDB analysis:

```cmd
scripts\run_full.cmd
```

Build or rebuild follow indexes:

```cmd
scripts\build_follow_indexes.cmd
scripts\build_follow_indexes.cmd --rebuild
```

Run indexed follow queries:

```cmd
scripts\query_indexed_follows.cmd
```

Run all seven advanced workstation analyses:

```cmd
scripts\run_remaining_paper_tasks.cmd
```

Run or validate the full independent Leiden result:

```cmd
python -m blue_start.cli starterpack-leiden
python -m blue_start.cli plot leiden
```

The full 1,997,488-node, 245,669,033-edge giant component was successfully
clustered with a memory-safe 32-bit C/igraph Leiden backend. It produced 740
communities with modularity `0.661649` and used about 8.2 GB of process working
memory. Agreement with the paper's published partition is NMI `0.873341` and
adjusted Rand index `0.843200`.

## 12. Testing

The Python package, data validation, analysis functions, output generation,
partitioned index writer, indexed query macros, union-find, compact k-core,
entropy, and configuration-model shuffle invariants were tested.

All thirteen automated tests pass.

The follow-index implementation was first verified on a small graph. The full
source and destination indexes were then built successfully from all
2,416,311,437 edges, and the stored macros were benchmarked against the final
files.

## 13. GitHub-ready copy

A smaller GitHub-ready project copy was created at:

```text
E:\blue-start-duckdb
```

It includes source code, configuration, documentation, tests, figures,
summaries, and small outputs.

It excludes:

- raw data;
- the local DuckDB database;
- temporary spill files;
- the 20.28 GiB follow indexes;
- individual output files larger than GitHub's normal file limit.

These large local files can be recreated by running the documented commands.

## 14. Final status

The complete 2.4-billion-edge dataset was successfully scanned, aggregated,
joined, analyzed, and indexed on a 32 GB computer.

DuckDB made this possible by processing data from disk instead of loading the
entire graph into memory.

The project now supports:

- reproducible full-dataset analytics;
- cached statistical results;
- generated research figures;
- official reference results for independent validation;
- fast interactive outgoing and incoming follow queries;
- edge checks, mutual follows, and common-follow queries;
- exact full-network WCC;
- exact full-network SCC;
- exact hypergraph k-core;
- independently recomputed edge entropy;
- a full degree- and edge-size-preserving configuration model;
- full paper-compatible pair co-occurrence;
- the full disk-backed weighted clique projection;
- exact unrestricted s-line counts for all 345 thresholds.

No major paper analysis or exact-reproduction task remains unimplemented.
The original Python `igraph`/`leidenalg` clustering was subsequently completed
with a fixed 120 GiB Windows page file. It used up to about 47.05 GiB of private
commit, completed the complete original pipeline in 51.44 minutes, produced
503 communities, and matched every published node assignment exactly. The
separate 740-community C/igraph result remains available as an independent,
lower-memory partition.
