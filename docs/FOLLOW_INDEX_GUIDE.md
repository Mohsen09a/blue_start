# Fast follow-graph queries

The `follows` relation contains approximately 2.416 billion edges and is an
external view over a 12.56 GB Parquet file. DuckDB cannot attach an ART index
directly to that view. Materializing one table and building two ART indexes
over 2.416 billion rows would also be risky on a 32 GB machine.

The project therefore uses disk-backed adjacency indexes. It writes two
hash-partitioned Parquet datasets:

- `work/follow_indexes/by_src`: outgoing edges for `follows_of(person_id)`;
- `work/follow_indexes/by_dst`: incoming edges for `followers_of(person_id)`.

Each query reads one of 256 partitions instead of scanning the complete edge
file. The same indexes also support pair checks, mutual follows, common follows,
and date filters within one person's adjacency list.

## Build

Close any program that currently has the DuckDB database open, then run:

```cmd
cd /d E:\blue-start-duckdb
scripts\build_follow_indexes.cmd
```

Build only outgoing or incoming adjacency:

```cmd
scripts\build_follow_indexes.cmd --direction src
scripts\build_follow_indexes.cmd --direction dst
```

The complete build scans the source twice and is expected to create roughly
25-35 GB of additional files. Runtime depends heavily on SSD throughput. Do
not change the default partition count after building one direction.

## Indexed queries

People followed by a person:

```sql
SELECT *
FROM follows_of(21486540)
ORDER BY date_followed DESC
LIMIT 20;
```

People following a person:

```sql
SELECT *
FROM followers_of(21486540)
ORDER BY date_followed DESC
LIMIT 20;
```

Check one directed edge:

```sql
SELECT is_following(21486540, 3528659);
```

Mutual follows:

```sql
SELECT *
FROM mutual_follows_of(21486540)
LIMIT 20;
```

Accounts followed by both people:

```sql
SELECT *
FROM common_follows_of(21486540, 3528659)
LIMIT 20;
```

Outgoing follows during a date range:

```sql
SELECT *
FROM follows_of(21486540)
WHERE date_followed >= DATE '2025-01-01'
  AND date_followed < DATE '2026-01-01';
```

For global date totals, use the already materialized
`results.follow_volume_full` table instead of rescanning individual edges.
