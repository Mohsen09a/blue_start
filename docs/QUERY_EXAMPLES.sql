-- Blue Start DuckDB query examples
--
-- Database: E:\blue-start-duckdb\work\blue_start.duckdb
-- Verified sample person: 21486540
--
-- The identifiers are deidentified numeric IDs. The dataset does not contain
-- Bluesky handles or display names.

-- 1. Fast: inspect one person and their precomputed degrees.
SELECT
    n.node_id,
    n.date_created,
    n.active,
    n.status,
    d.in_degree,
    d.out_degree
FROM nodes AS n
LEFT JOIN results.follow_degrees_full AS d USING (node_id)
WHERE n.node_id = 21486540;

-- 2. Full edge scan: people followed by the sample person.
-- On the tested 32 GB machine, this returned 20 rows in about 5.57 seconds.
SELECT
    f.dst AS followed_person_id,
    f.date_followed,
    n.date_created,
    n.active,
    n.status
FROM follows AS f
LEFT JOIN nodes AS n
    ON n.node_id = f.dst
WHERE f.src = 21486540
ORDER BY f.date_followed DESC
LIMIT 20;

-- 3. Full edge scan: people who follow the sample person.
SELECT
    f.src AS follower_id,
    f.date_followed,
    n.date_created,
    n.active,
    n.status
FROM follows AS f
LEFT JOIN nodes AS n
    ON n.node_id = f.src
WHERE f.dst = 21486540
ORDER BY f.date_followed DESC
LIMIT 20;

-- 4. Fast: get the person's counts without rescanning the edge file.
SELECT node_id, in_degree, out_degree
FROM results.follow_degrees_full
WHERE node_id = 21486540;

-- 5. Full edge scan: monthly following activity for the sample person.
SELECT
    date_trunc('month', date_followed)::DATE AS month,
    count(*) AS follows_created
FROM follows
WHERE src = 21486540
GROUP BY month
ORDER BY month;

-- 6. Fast: Starter Packs containing the sample person.
SELECT
    m.pack_id,
    m.date_added,
    p.creator_id,
    p.date_created AS pack_created,
    p.member_count
FROM starterpack_memberships AS m
JOIN starterpacks AS p USING (pack_id)
WHERE m.member_id = 21486540
ORDER BY m.date_added DESC;

-- 7. People who share Starter Packs with the sample person.
SELECT
    other.member_id,
    count(DISTINCT mine.pack_id) AS shared_pack_count
FROM starterpack_memberships AS mine
JOIN starterpack_memberships AS other
    ON other.pack_id = mine.pack_id
   AND other.member_id <> mine.member_id
WHERE mine.member_id = 21486540
GROUP BY other.member_id
ORDER BY shared_pack_count DESC, other.member_id
LIMIT 20;

-- 8. Fast: globally highest out-degree people from cached results.
SELECT node_id, out_degree, in_degree
FROM results.follow_degrees_full
ORDER BY out_degree DESC
LIMIT 20;

-- 9. Fast: globally highest in-degree people from cached results.
SELECT node_id, in_degree, out_degree
FROM results.follow_degrees_full
ORDER BY in_degree DESC
LIMIT 20;

-- 10. Fast: follow volume by day from cached results.
SELECT date_followed, follow_count
FROM results.follow_volume_full
ORDER BY follow_count DESC
LIMIT 20;

-- Add EXPLAIN ANALYZE before a query to see its execution plan and timing.
EXPLAIN ANALYZE
SELECT dst, date_followed
FROM follows
WHERE src = 21486540
LIMIT 20;
