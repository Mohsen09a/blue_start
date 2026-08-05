# Exact Original-Backend Leiden Reproduction

## Result

The paper repository's original `get_starterpack_clustering.py` algorithm was
executed successfully on the 32 GB workstation by adding a fixed 120 GiB
Windows page file on drive E.

Only input and output filesystem paths were adapted. The following upstream
operations were unchanged:

- XGI HIF loading;
- largest connected hypergraph extraction;
- Python-set clique expansion;
- 64-bit Python igraph graph construction;
- `leidenalg.find_partition`;
- `ModularityVertexPartition`;
- random seed `0`.

## Exact validation

| Item | Result |
|---|---:|
| Giant-component nodes | 1,997,488 |
| Clique-projection edges | 245,669,033 |
| Communities | 503 |
| Largest community | 434,207 |
| Published node-set match | exact |
| Published assignment match | exact |
| Total runtime | 51.44 minutes |
| Highest observed private commit | 47.05 GiB |
| Lowest observed available physical RAM | 0.12 GiB |
| Configured system commit limit | 155.22 GiB |

Every node received exactly the same community ID as the published
`node_labels.json`; this is stronger than agreement up to a label permutation.

The five largest communities were:

```text
434,207
333,148
156,110
126,567
98,434
```

## Environment

```text
Python:     3.12
igraph:     0.11.9
leidenalg:  0.10.2
xgi:        0.10.2
Page file:  120 GiB fixed on E
```

The original Python algorithm reached about 47 GiB of private committed
memory. It therefore could not fit in 32 GiB of physical RAM alone. Windows
paged inactive memory to E while retaining enough active graph data in RAM.

## Isolated run directory

```text
reference/upstream-a-blue-start/original_swap_run/
```

The official upstream result was not overwritten. Execution logs and the
locally generated JSON remain in the isolated output directory.

## Portable outputs

```text
outputs/parquet/starterpack_leiden_labels_paper_backend_local.parquet
outputs/parquet/starterpack_leiden_community_sizes_paper_backend_local.parquet
outputs/summaries/starterpack_leiden_original_backend.json
```

## Comparison with the memory-safe backend

The earlier 32-bit C/igraph run remains useful as an independent community
detection result:

| Backend | Communities | Runtime |
|---|---:|---:|
| Original Python `leidenalg` | 503 | 51.44 minutes total pipeline |
| Memory-safe C/igraph | 740 | 11.46 minutes for Leiden |

The C/igraph result had NMI `0.873341` and adjusted Rand index `0.843200`
against the exact published partition.

