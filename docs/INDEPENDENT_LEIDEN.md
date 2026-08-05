# Full Independent Leiden Community Detection

## Status

The full Starter Pack clique-projection giant component was clustered
independently on the 32 GB workstation. This is a full-graph result, not a
sample.

| Item | Value |
|---|---:|
| Projection nodes | 2,003,530 |
| Giant-component nodes | 1,997,488 |
| Giant-component edges | 245,669,033 |
| Independent communities | 740 |
| Largest community | 434,820 |
| Modularity | 0.661649 |
| Leiden runtime | 687.64 seconds |
| Observed process working set | about 8.2 GB |

The independently computed giant-component node set exactly matches the node
set used by the paper.

## Why a native 32-bit backend was necessary

The paper uses Python `igraph` plus `leidenalg`. On this machine, constructing
the full 64-bit Python graph raised total system RAM use to 95.9% before Leiden
started. That attempt was stopped safely without changing completed results.

The successful implementation uses:

- an unweighted, undirected graph, matching the paper;
- modularity optimization;
- random seed `0`;
- two Leiden iterations;
- beta `0.01`;
- C/igraph 0.10.16 compiled with 32-bit graph integers.

The graph is within the 32-bit igraph edge limit. Four-byte graph integers cut
the largest graph arrays roughly in half and keep the run safe on the available
RAM.

The native runner is `native/leiden32.c`. It reads the compact binary edge
array, constructs the full graph, runs `igraph_community_leiden`, and writes
one 32-bit community ID per node.

## Compatibility note

The paper calls the separate C++ `leidenalg` implementation through Python.
This project uses C/igraph's built-in Leiden implementation for the full,
memory-safe run. The objective and main parameters match, but the backend does
not. Leiden can follow a different optimization path, so the partitions are
not expected to be identical.

| Comparison metric | Result |
|---|---:|
| Paper communities | 503 |
| Independent communities | 740 |
| Normalized mutual information | 0.873341 |
| Adjusted Rand index | 0.843200 |

The agreement is strong, but this result must not be described as an exact
reproduction of the paper's community IDs.

## Subsequent exact-backend result

The original Python `igraph`/`leidenalg` implementation was subsequently run
with a fixed 120 GiB Windows page file. It completed in 51.44 minutes and
matched every published node assignment exactly. See
[Exact Original-Backend Leiden Reproduction](ORIGINAL_BACKEND_LEIDEN.md).

The independent 32-bit C/igraph result documented here remains a separate,
lower-memory partition and is intentionally preserved.

## Running and reusing the result

Validate and import the completed native membership:

```cmd
cd /d E:\blue-start-duckdb
python -m blue_start.cli starterpack-leiden --import-native
```

The normal command also chooses the safe native result:

```cmd
python -m blue_start.cli starterpack-leiden
```

In the GitHub-ready repository, the large native membership under `work/` is
excluded. The same command automatically imports and validates the committed
portable Leiden label and community-size Parquet files instead.

The unsafe 64-bit Python backend is never selected implicitly. It requires:

```cmd
python -m blue_start.cli starterpack-leiden --python-backend
```

Do not use that flag on this 32 GB machine.

## Outputs

Persistent outputs:

```text
outputs/parquet/starterpack_leiden_labels_local.parquet
outputs/parquet/starterpack_leiden_community_sizes_local.parquet
outputs/summaries/starterpack_leiden_local.json
results.starterpack_leiden_labels_local
results.starterpack_leiden_community_sizes_local
```

Large, rebuildable working files:

```text
work/starterpack_leiden/giant_node_ids.uint32
work/starterpack_leiden/giant_unweighted_edges.uint32
work/starterpack_leiden/native32_membership.int32
work/starterpack_leiden/native32.stdout.log
```

The edge array is about 1.83 GiB. These working files should remain excluded
from Git.

## Downstream analyses

```cmd
python -m blue_start.cli edge-entropy --label-source independent
python -m blue_start.cli configuration-model --label-source independent --swaps-per-edge 10 --seed 0
python -m blue_start.cli plot leiden
python -m blue_start.cli plot mesoscale
```

Independent-label outputs are stored separately from earlier official-label
outputs, so the two analyses cannot be confused.
