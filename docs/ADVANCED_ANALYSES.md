# Advanced Analyses Completed Locally

This document records the seven major paper analyses added after the initial
DuckDB pipeline. All seven were executed on the 32 GB workstation.

## Exact weakly connected components

Command:

```cmd
python -m blue_start.cli follow-wcc
```

Method:

- Stream `src` and `dst` from the complete external Parquet relation.
- Treat every directed edge as an undirected edge.
- Process edges in batches of four million.
- Merge endpoints using a Numba-compiled union-find.
- Keep only compact `uint32` parent and size arrays plus a `uint8` seen array.
- Compress paths and calculate the final component-size distribution.

Results:

```text
Edges processed:             2,416,311,437
Nodes in the follow graph:      36,447,725
Weak components:                     6,735
Largest component:              36,433,172
Runtime:                            105.53 seconds
```

The complete local distribution exactly matches the official imported WCC
distribution.

Outputs:

```text
outputs/parquet/follow_wcc_distribution_local.parquet
outputs/summaries/follow_wcc_local.json
results.follow_wcc_distribution_local
```

## Exact strongly connected components

Command:

```cmd
python -m blue_start.cli follow-scc
```

Method:

- Reuse the 256 source and 256 destination follow-index partitions.
- Build forward and reverse CSR arrays as memory-mapped files.
- Store neighbors as `uint32` and offsets as `uint64`.
- Flush and checkpoint every partition.
- Reset and overwrite a partially written partition safely after interruption.
- Validate every final cursor against the cached in/out-degree totals.
- Run iterative, Numba-compiled Kosaraju traversals without recursion.
- Exclude numeric IDs with no follow edge, matching the paper graph.
- Compare the complete local distribution with the official distribution.

Results:

```text
Edges:                      2,416,311,437
Active graph nodes:            36,447,725
Strong components:             15,883,064
Largest component:             20,495,220
Forward CSR bucket time:         1,400.19 seconds
Reverse CSR bucket time:         1,168.05 seconds
Forward DFS:                       460.87 seconds
Reverse DFS:                        23.99 seconds
Full recorded run:               3,060.76 seconds
Reference differences:                  0 rows
```

The full local SCC component-size distribution exactly matches the official
reference result.

The retained forward and reverse CSR files use approximately 20 GB. They make
the completed computation reproducible without rebuilding adjacency.

Outputs:

```text
outputs/parquet/follow_scc_distribution_local.parquet
outputs/summaries/follow_scc_exact.json
results.follow_scc_distribution_local
work/follow_scc_exact/
```

## Exact compact hypergraph k-core

Command:

```cmd
python -m blue_start.cli starterpack-kcore
```

Method:

- Deduplicate `(pack_id, member_id)` records to match XGI set semantics.
- Convert user IDs to dense integer IDs.
- Build edge-to-node and node-to-edge CSR arrays.
- Use a deterministic Numba heap-based peeling algorithm.
- Remove collapsed hyperedges and update the remaining node degree.
- Save both per-node core numbers and their distribution.

Results:

```text
Nodes:                         2,003,536
Starter Packs:                   365,842
Deduplicated incidences:      12,703,609
Maximum core number:              34,297
Nodes with core >= 1,000:             772
Runtime:                              5.16 seconds
```

The maximum core and `core >= 1,000` result match the official output. The
local positive-core population is 13 nodes larger because the supplied local
snapshot is newer than the paper snapshot.

Outputs:

```text
outputs/parquet/starterpack_kcore_local.parquet
outputs/parquet/starterpack_kcore_distribution_local.parquet
outputs/summaries/starterpack_kcore_local.json
results.starterpack_kcore_local
results.starterpack_kcore_distribution_local
```

## Independently recomputed edge entropy

Command:

```cmd
python -m blue_start.cli edge-entropy
```

Method:

- Load the official Leiden community labels for the giant component.
- Deduplicate Starter Pack memberships.
- Select packs for which every unique member has a community label.
- Count members in each community for each pack.
- Calculate Shannon entropy.
- Normalize by `log(pack_size)`, matching the paper notebook.

Results:

```text
Labeled nodes:                  1,997,488
Communities:                          503
Fully labeled packs:              365,157
Mean normalized entropy:         0.160416
Zero-entropy fraction:           0.170732
Runtime:                              2.61 seconds
```

This independently reproduces the paper's reported mean of approximately
`0.160`. The community labels are official imported labels; the entropy values
are locally recomputed.

Outputs:

```text
outputs/parquet/starterpack_edge_entropy_local.parquet
outputs/summaries/starterpack_edge_entropy_local.json
results.starterpack_edge_entropy_local
```

### Entropy from the independent full Leiden result

```cmd
python -m blue_start.cli edge-entropy --label-source independent
```

This analyzed 365,157 fully labeled packs using 740 independently computed
communities. Mean normalized entropy was `0.138833`, and the zero-entropy
fraction was `0.222189`. Outputs use the suffix `independent`, so they do not
overwrite the corresponding official-label analysis.

## Configuration-model randomization

Command:

```cmd
python -m blue_start.cli configuration-model --swaps-per-edge 10 --seed 0
```

Method:

- Use the same fully labeled giant-component packs as the entropy calculation.
- Reimplement XGI's `random_edge_shuffle` with packed arrays.
- Select two random hyperedges.
- Preserve nodes shared by both edges.
- Randomly redistribute their other members.
- Preserve every hyperedge size and every node degree.
- Perform ten shuffle attempts per hyperedge.
- Recalculate normalized entropy after randomization.

Results:

```text
Packs:                           365,157
Deduplicated incidences:      12,697,223
Shuffle attempts:             3,651,570
Node degrees preserved:             yes
Edge sizes preserved:               yes
Mean normalized entropy:         0.575723
Runtime:                             11.40 seconds
```

This reproduces the paper's reported randomized mean of approximately `0.576`.

Outputs:

```text
outputs/parquet/starterpack_configuration_entropy_local.parquet
outputs/summaries/starterpack_configuration_entropy_local.json
results.starterpack_configuration_entropy_local
```

The matching independent-label randomization is:

```cmd
python -m blue_start.cli configuration-model --label-source independent --swaps-per-edge 10 --seed 0
```

It preserved all node degrees and hyperedge sizes. Its mean normalized entropy
was `0.563722`, compared with the observed independent-label mean of
`0.138833`.

## Full independent Leiden clustering

The complete 245,669,033-edge giant component was clustered locally with a
memory-safe 32-bit C/igraph backend. The result contains 740 communities and
has modularity `0.661649`. See
[Full Independent Leiden Community Detection](INDEPENDENT_LEIDEN.md) for the
method, resource measurements, compatibility limitation, and output paths.

## Full paper-compatible pair co-occurrence

Command:

```cmd
python -m blue_start.cli pair-cooccurrence-paper
```

Method:

- Deduplicate memberships to match XGI hyperedge-set semantics.
- Generate every pair exactly for packs with at most 4,069 unique members.
- Sample and scale pairs only for packs above that threshold.
- Hash-partition pair occurrences into 256 temporary Parquet partitions.
- Aggregate one partition at a time to keep memory bounded.
- Apply Python-compatible round-to-even behavior to sampled weights.
- Remove temporary pair rows after the final distribution is saved.

The supplied local data has no deduplicated pack above 4,069 members.
Therefore, the completed run is exact for every local pack.

Results:

```text
Nonempty packs:                 365,611
Unique memberships:         12,703,609
Exact pair occurrences:    415,832,406
Distinct user pairs:       245,754,884
Maximum co-occurrence:          32,696
Temporary pair storage:        2.60 GB
Runtime:                       58.40 seconds
```

The complete local distribution is identical to the official imported
distribution.

Outputs:

```text
outputs/parquet/pair_cooccurrence_paper_compatible.parquet
outputs/summaries/pair_cooccurrence_paper_compatible.json
results.pair_cooccurrence_paper_compatible
```

## Full disk-backed weighted clique projection

Command:

```cmd
python -m blue_start.cli clique-projection
```

Method:

- Reuse or generate the 256 hash-partitioned pair-occurrence files.
- Aggregate each partition independently by `(node_a, node_b)`.
- Store co-occurrence as the projected edge weight.
- Write 256 Zstandard-compressed Parquet projection files.
- Expose the files through `starterpack_clique_projection`.
- Scan the disk-backed relation to calculate projected degree and strength.
- Validate the edge count against the pair-co-occurrence distribution.
- Remove the larger pair-occurrence intermediate after success.

Results:

```text
Projected nodes:                 2,003,530
Weighted projected edges:      245,754,884
Maximum projected degree:          912,507
Maximum node strength:            5,269,118
Maximum edge weight:                 32,696
Projection files:                        256
Projection storage:        1,789,588,433 bytes
Runtime:                            97.84 seconds
```

Outputs:

```text
work/clique_projection/partitions_256/
outputs/parquet/starterpack_projection_node_stats_local.parquet
outputs/parquet/starterpack_projection_degree_distribution_local.parquet
outputs/summaries/starterpack_weighted_clique_projection.json
starterpack_clique_projection
results.starterpack_projection_node_stats_local
results.starterpack_projection_degree_distribution_local
```

## Exact unrestricted full s-line

Command:

```cmd
python -m blue_start.cli s-line-full
```

The native checkpointed implementation computed every threshold from `s=1`
through `s=345` without filtering or sampling. It counted 19,559,507,901
distinct pack pairs at `s=1`, completed in 39.83 seconds, and exactly matched
all 345 official rows. See [Exact Unrestricted Full s-Line](FULL_SLINE.md).

## Run all advanced analyses

```cmd
cd /d E:\blue-start-duckdb
scripts\run_remaining_paper_tasks.cmd
```

The implementation is located in:

```text
src/blue_start/advanced.py
```

The low-level algorithms have automated tests in:

```text
tests/test_advanced.py
```
