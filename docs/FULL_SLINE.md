# Exact Unrestricted Full s-Line Calculation

## Status

The unrestricted s-line statistics were computed exactly for every threshold
from `s=1` through `s=345` on the 32 GB workstation.

| Item | Result |
|---|---:|
| Starter Packs | 365,842 |
| Hypergraph nodes | 2,003,536 |
| Deduplicated incidences | 12,703,609 |
| Threshold range | 1 through 345 |
| Distinct pack pairs at `s=1` | 19,559,507,901 |
| Active packs at `s=1` | 365,228 |
| Runtime | 39.83 seconds |
| Checkpoint batches | 90 |
| Rows differing from the official output | 0 |

This is the complete calculation. No high-degree members, hyperedges, or pack
pairs were filtered or sampled.

## Why the direct implementation was unsafe

The paper builds an inverted index and creates a candidate counter for every
pair of Starter Packs sharing a member. One member occurs in 175,159 packs and
alone induces more than 15 billion candidate pairs. Materializing those pairs
as Python objects or DuckDB rows would exceed workstation memory or create a
very large disk intermediate.

## Memory-safe algorithm

The local implementation stores the hypergraph in two compact CSR layouts:

- pack to member;
- member to pack.

For each source pack, the native C kernel:

1. visits all of its members;
2. visits the later packs containing each member;
3. increments a reusable dense `uint16` overlap counter;
4. records only the touched pack IDs;
5. adds each final overlap to a small histogram;
6. records the maximum overlap partner for both endpoints;
7. resets only the touched counters.

After all source packs are processed:

- reverse cumulative sums of the exact-overlap histogram give the edge count
  for every threshold;
- counting packs whose maximum partner overlap is at least `s` gives the active
  node count for every threshold.

This produces the paper's requested summary without materializing the 19.6
billion line-graph edges.

## Checkpointing and safety

The 365,842 source packs are divided into 90 batches of at most 4,096 packs.
Every batch writes:

- an exact-overlap histogram;
- a maximum-partner-overlap array;
- a text execution log.

Files are first written with a temporary suffix and renamed only after their
sizes are validated. Interrupted runs reuse completed batches.

The native worker uses eight threads. Each thread owns bounded reusable arrays,
so memory use does not grow with the number of line-graph edges.

## Validation

Validation was performed at three levels:

1. the native kernel exactly matched a hand-checkable four-pack toy graph;
2. all batch files were checked for their expected byte sizes before merging;
3. all 345 locally computed `(s, nodes, edges)` rows exactly matched the
   official output.

The automated Python test suite also passes.

## Commands

Run or reuse the complete result:

```cmd
cd /d E:\blue-start-duckdb
python -m blue_start.cli s-line-full
```

Run only a bounded number of new batches:

```cmd
python -m blue_start.cli s-line-full --maximum-new-batches 1
```

Validate the native kernel on the toy hypergraph:

```cmd
python scripts\validate_sline_native.py
```

Rebuild all input and checkpoints:

```cmd
python -m blue_start.cli s-line-full --rebuild
```

## Implementation and outputs

```text
native/sline_full.c
src/blue_start/sline.py
scripts/validate_sline_native.py
outputs/parquet/s_line_full_local.parquet
outputs/summaries/s_line_full_local.json
results.s_line_full_local
work/sline_full/
```

The `work/sline_full` directory uses about 189 MB and is rebuildable.
