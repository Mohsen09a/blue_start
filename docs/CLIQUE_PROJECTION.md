# Full Weighted Clique Projection

## Purpose

The projection converts the Starter Pack hypergraph into a weighted ordinary
graph. Two users are connected when they occur together in at least one
Starter Pack. Their edge weight is the number of packs they share.

## Why it fits on this workstation

The final graph has 245,754,884 unique edges, which is too large to represent
comfortably as Python objects on a 32 GB computer. The implementation therefore
keeps the edge relation in compressed Parquet and processes one of 256 hash
partitions at a time.

No step loads the complete projected graph into RAM.

## Build process

1. Deduplicate `(pack_id, member_id)` memberships.
2. Generate all user-pair occurrences inside each pack.
3. Assign each pair to `hash(node_a, node_b) % 256`.
4. Write temporary compressed pair-occurrence partitions.
5. Group one partition at a time by `(node_a, node_b)`.
6. Sum the occurrences to obtain the edge weight.
7. Save each final projection partition as compressed Parquet.
8. Install a DuckDB view over all final partitions.
9. Compute per-node projected degree and weighted strength.
10. Verify that the projected edge count equals the pair-distribution total.
11. Remove the 2.60 GB temporary occurrence dataset.

Because the largest deduplicated local pack has 4,069 members, every pack was
processed exactly. The sampling fallback was not used for this dataset.

## Results

| Measurement | Value |
|---|---:|
| Projected nodes | 2,003,530 |
| Unique weighted edges | 245,754,884 |
| Maximum projected degree | 912,507 |
| Maximum weighted strength | 5,269,118 |
| Maximum edge weight | 32,696 |
| Final partitions | 256 |
| Final storage | 1,789,588,433 bytes |
| Runtime | 97.84 seconds |

## Commands

Build or reuse the completed projection:

```cmd
cd /d E:\blue-start-duckdb
scripts\build_clique_projection.cmd
```

Force a complete projection rebuild:

```cmd
scripts\build_clique_projection.cmd --rebuild-projection
```

Render its figure:

```cmd
python -m blue_start.cli plot projection
```

Run a partition-pruned edge lookup:

```cmd
scripts\query_projection.cmd 21486540 3528659
```

## Query examples

Top weighted edges:

```sql
SELECT node_a, node_b, cooccurrence
FROM starterpack_clique_projection
ORDER BY cooccurrence DESC
LIMIT 20;
```

Top nodes by number of projected neighbors:

```sql
SELECT node_id, degree, strength
FROM results.starterpack_projection_node_stats_local
ORDER BY degree DESC
LIMIT 20;
```

Lookup the edge between two users:

```sql
SELECT cooccurrence
FROM starterpack_clique_projection
WHERE pair_bucket = 239
  AND node_a = least(21486540, 3528659)
  AND node_b = greatest(21486540, 3528659);
```

The first two examples may scan much of the projection. The point lookup reads
only one hash partition because the bucket predicate is supplied explicitly.
Calculate the bucket for another pair with:

```sql
SELECT hash(
    least(21486540, 3528659),
    greatest(21486540, 3528659)
) % 256 AS pair_bucket;
```

## Generated figure

```text
outputs/figures/starterpack_clique_projection.png
outputs/figures/starterpack_clique_projection.pdf
```

The left panel shows projected-degree frequency. The right panel shows the
weighted-edge distribution.
