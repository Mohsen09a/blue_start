# How the Follow Indexes Were Built

## The original problem

The follow dataset contains approximately 2.416 billion directed edges.

Each edge has three values:

```text
src             Person who follows
dst             Person being followed
date_followed   Date when the follow was created
```

The data is stored in one 12.56 GB Parquet file. The DuckDB `follows` object is
a view over this file, not a normal materialized table.

Before indexing, a query for one person could scan a large part of the complete
file:

```sql
SELECT dst, date_followed
FROM follows
WHERE src = 21486540;
```

DuckDB cannot create a normal ART index directly on an external Parquet view.
Materializing 2.416 billion rows and building multiple ART indexes would also
be expensive and risky on a computer with 32 GB of RAM.

## The selected solution

I created two disk-backed copies of the edge data:

- an outgoing index partitioned using `src`;
- an incoming index partitioned using `dst`.

Each index has 256 hash buckets.

This means that a person-level query reads one relevant bucket instead of
scanning the complete source file.

## Building the outgoing index

For every edge, DuckDB calculated:

```sql
hash(src) % 256
```

This produces a bucket number between 0 and 255.

The outgoing index was created with the equivalent of this SQL:

```sql
COPY (
    SELECT
        src,
        dst,
        date_followed,
        (hash(src) % 256)::USMALLINT AS src_bucket
    FROM follows
)
TO 'E:/blue-start-duckdb/work/follow_indexes/by_src'
(
    FORMAT PARQUET,
    PARTITION_BY (src_bucket),
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 122880
);
```

DuckDB streamed the edges from the original file, calculated the bucket for
each edge, compressed the data, and wrote 256 Parquet partitions.

The result was:

```text
Files:       256
Size:        8.95 GiB
Build time:  122.09 seconds
Location:    E:\blue-start-duckdb\work\follow_indexes\by_src
```

## Building the incoming index

The incoming index used the destination person:

```sql
hash(dst) % 256
```

It was created with the equivalent of this SQL:

```sql
COPY (
    SELECT
        src,
        dst,
        date_followed,
        (hash(dst) % 256)::USMALLINT AS dst_bucket
    FROM follows
)
TO 'E:/blue-start-duckdb/work/follow_indexes/by_dst'
(
    FORMAT PARQUET,
    PARTITION_BY (dst_bucket),
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 122880
);
```

The result was:

```text
Files:       256
Size:        11.33 GiB
Build time:  206.73 seconds
Location:    E:\blue-start-duckdb\work\follow_indexes\by_dst
```

Together, both indexes use approximately 20.28 GiB.

## Registering the indexed files in DuckDB

The outgoing partitions were registered as a DuckDB view:

```sql
CREATE OR REPLACE VIEW indexed_follows_by_src AS
SELECT
    src::UINTEGER AS src,
    dst::UINTEGER AS dst,
    date_followed::DATE AS date_followed,
    src_bucket::USMALLINT AS src_bucket
FROM read_parquet(
    'E:/blue-start-duckdb/work/follow_indexes/by_src/**/*.parquet',
    hive_partitioning = true
);
```

The incoming partitions were registered in the same way:

```sql
CREATE OR REPLACE VIEW indexed_follows_by_dst AS
SELECT
    src::UINTEGER AS src,
    dst::UINTEGER AS dst,
    date_followed::DATE AS date_followed,
    dst_bucket::USMALLINT AS dst_bucket
FROM read_parquet(
    'E:/blue-start-duckdb/work/follow_indexes/by_dst/**/*.parquet',
    hive_partitioning = true
);
```

`hive_partitioning = true` allows DuckDB to read the bucket value from each
partition directory.

## Creating the query macros

I created a macro for outgoing follows:

```sql
CREATE OR REPLACE MACRO follows_of(p_person_id) AS TABLE
SELECT dst, date_followed
FROM indexed_follows_by_src
WHERE src_bucket =
        (hash(p_person_id::UINTEGER) % 256)::USMALLINT
  AND src = p_person_id::UINTEGER;
```

When it receives a person ID, the macro:

1. calculates the person's bucket;
2. opens that bucket;
3. filters the bucket to the exact person;
4. returns the person's outgoing edges.

The incoming-follower macro works in the opposite direction:

```sql
CREATE OR REPLACE MACRO followers_of(p_person_id) AS TABLE
SELECT src, date_followed
FROM indexed_follows_by_dst
WHERE dst_bucket =
        (hash(p_person_id::UINTEGER) % 256)::USMALLINT
  AND dst = p_person_id::UINTEGER;
```

## Additional macros

### Check one follow relationship

```sql
SELECT is_following(21486540, 3528659);
```

This uses the outgoing index and checks whether one matching destination
exists.

### Find mutual follows

```sql
SELECT *
FROM mutual_follows_of(21486540);
```

This joins the person's outgoing edges with the person's incoming edges. A
matching ID means that both accounts follow each other.

### Find common follows

```sql
SELECT *
FROM common_follows_of(21486540, 3528659);
```

This reads the outgoing bucket for each person and joins their destination
IDs. Matching destinations are accounts followed by both people.

## Why the queries became faster

Without the new layout, DuckDB could scan the complete 12.56 GB edge file.

With 256 buckets, a normal person-level query usually reads approximately
one two-hundred-and-fifty-sixth of an index.

The exact amount varies because bucket sizes are not perfectly equal, but the
query normally reads tens of megabytes instead of many gigabytes.

Measured results on the full dataset were:

| Query | Indexed time |
|---|---:|
| Outgoing follows ordered by date | 1.1123 seconds |
| Incoming followers ordered by date | 0.0532 seconds |
| One edge-existence check | 0.0260 seconds |
| Incoming and outgoing counts | 0.0582 seconds |
| Mutual follows | 0.0750 seconds |
| Common follows | 0.0550 seconds |

The outgoing example is slower because the selected sample person has 844,408
outgoing edges and the query sorts those edges by date.

## Memory behavior

The complete graph was not loaded into RAM.

During the build, DuckDB:

1. streamed rows from the original Parquet file;
2. calculated hashes in batches;
3. compressed output batches;
4. wrote the partitions to disk.

The project limits DuckDB to 18 GB of RAM and allows temporary disk spilling.
This made the full index build possible on the 32 GB computer.

## Implementation files

The index builder is:

```text
E:\blue-start-duckdb\scripts\build_follow_indexes.py
```

Its CMD wrapper is:

```text
E:\blue-start-duckdb\scripts\build_follow_indexes.cmd
```

The indexed-query test runner is:

```text
E:\blue-start-duckdb\scripts\query_indexed_follows.py
```

## Commands

Build both indexes:

```cmd
cd /d E:\blue-start-duckdb
scripts\build_follow_indexes.cmd
```

Run the indexed queries:

```cmd
scripts\query_indexed_follows.cmd --limit 20
```

Rebuild the indexes only when necessary:

```cmd
scripts\build_follow_indexes.cmd --rebuild
```

The partition count must remain 256 unless both indexes are rebuilt with a new
value.
