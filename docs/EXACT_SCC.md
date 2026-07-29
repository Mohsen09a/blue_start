# Exact Full Follow-Network SCC

## Outcome

The strongly connected components of the complete directed follow network
were calculated exactly on the 32 GB workstation.

| Measurement | Value |
|---|---:|
| Directed edges | 2,416,311,437 |
| Nodes with at least one edge | 36,447,725 |
| Strongly connected components | 15,883,064 |
| Largest SCC | 20,495,220 |
| Full recorded runtime | 3,060.76 seconds |
| Differing official distribution rows | 0 |

The entire local component-size distribution exactly matches the official
published distribution.

## Disk-backed design

A normal Python graph object would require far more than 32 GB. The local
implementation instead uses two compact CSR relations:

```text
forward_offsets.uint64
forward_neighbors.uint32
reverse_offsets.uint64
reverse_neighbors.uint32
```

The two neighbor arrays contain 2,416,311,437 entries each and use about
9.67 GB each. NumPy memory maps expose them to Numba without loading both
files into RAM.

## Safe construction

The existing source and destination follow indexes are processed as 256
partitions per direction.

For every partition, the implementation:

1. reads Parquet in one-million-row batches;
2. scatters endpoints into the appropriate CSR slots;
3. checks for degree overflow;
4. flushes the memory map;
5. updates an atomic JSON checkpoint.

If a process stops during a partition, that partition resets its cursors and
overwrites its own CSR ranges when the command is resumed. Completed
partitions are not rebuilt.

After all partitions are written, every cursor is compared with the expected
offset derived from the complete cached degree table.

## Exact algorithm

The implementation runs iterative Kosaraju:

1. depth-first traversal on the forward graph to calculate finishing order;
2. traversal in reverse finishing order on the transpose graph;
3. count the nodes in every discovered strongly connected component;
4. aggregate the component-size distribution.

The traversals are Numba-compiled and use explicit compact stacks. Python
recursion and per-node Python objects are not used.

## Timing

```text
Forward CSR bucket time:    1,400.19 seconds
Reverse CSR bucket time:    1,168.05 seconds
Forward DFS:                  460.87 seconds
Reverse DFS:                   23.99 seconds
Full recorded run:          3,060.76 seconds
```

Peak observed working-set memory stayed below the configured 22 GB safety
threshold. Approximately 20 GB of reusable CSR files remain under
`work/follow_scc_exact`.

## Commands

Run or resume:

```cmd
cd /d E:\blue-start-duckdb
scripts\run_follow_scc.cmd
```

Run a bounded CSR probe:

```cmd
scripts\run_follow_scc.cmd --maximum-new-buckets 2
```

Force a complete rebuild:

```cmd
scripts\run_follow_scc.cmd --rebuild
```

The normal command now immediately reuses the completed result. Use
`--rebuild` only when the full CSR and SCC calculation must be repeated.

## Outputs

```text
outputs/parquet/follow_scc_distribution_local.parquet
outputs/summaries/follow_scc_exact.json
work/follow_scc_exact/
```

DuckDB table:

```text
results.follow_scc_distribution_local
```
