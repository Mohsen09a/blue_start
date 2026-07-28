# Technical Notes on *A Blue Start*

Reviewed reference: arXiv version `2505.11608v2`, dated February 3, 2026.

## Problem and Contribution

Most social-network datasets capture only pairwise relationships. This paper
publishes both Bluesky's directed follow network and its starter packs, which
are modeled as higher-order interactions. Anonymous user identifiers are
consistent across all released files, allowing the pairwise network and the
hypergraph to be linked directly.

## Data Collection Pipeline

1. Export DIDs, creation timestamps, and PDS addresses from the PLC Directory.
2. Retrieve repository listings from each PDS through `listRepos`.
3. Retrieve repositories for active users through `getRepo`.
4. Extract follow and starter-pack records.
5. Merge identifiers and anonymize each DID with a one-to-one integer mapping.

Of 7,731 PDS addresses, 3,163 were successfully queried. Of 36,689,265 requested
user repositories, 36,485,432 were retrieved successfully. Timestamp precision
was reduced to one day.

## Released Files and Their Relationships

- `nodes`: user identifier, creation date, activity flag, and account status;
- `starterpacks.jsonl`: pack identifier, creator, creation date, and members
  with their addition dates;
- `starterpack_edgelist`: hyperedge members without timestamps;
- `starterpack_hif`: a standard hypergraph representation with incidences and
  attributes;
- `follows`: triples of `(from, to, date_followed)`.

For joint temporal analysis, `starterpacks.jsonl` is preferable to the simple
edgelist because it preserves both pack creation dates and per-member addition
dates.

## Key Structural Findings

- The starter-pack size distribution has important modes at 8, 50, and 150.
- 84.3% of users in the starter-pack network did not create a starter pack.
- The largest hypergraph component contains 99.7% of starter-pack members.
- The `bsky.app` account appears in approximately 48% of starter packs, making
  the line graph extremely dense.
- 772 nodes have a coreness of at least 1,000.
- Leiden clustering on the projection produced 503 communities.
- Mean normalized pack entropy was 0.16, compared with 0.576 for the
  configuration model.
- The giant weakly connected component covers 99.96% of the follow-network
  nodes.
- Centrality rankings in the hypergraph and follow network are only moderately
  correlated, indicating complementary structural information.

## Important Limitations

- The release is a snapshot rather than a complete temporal history; unfollows,
  deleted packs, and accounts removed before collection may be missing.
- Some PDS instances did not respond.
- Eight starter packs in the paper snapshot have physically impossible dates.
- 147,655 follow events, approximately 0.006%, precede the creation date of at
  least one endpoint.
- AT Protocol users can manually modify some timestamps.
- Very large packs exist because list-to-pack conversion tools can bypass the
  normal user-interface limits.

The locally supplied November dataset is newer than the paper snapshot. It
contains a maximum pack size of 4,661 rather than the paper's reported 4,069,
and nine negative creator ages rather than eight.

## Computational Implications

The decompressed follow CSV is approximately 71 GB and contains 2.4 billion
rows. The paper reports:

- more than 200 GB of RAM to load it with pandas;
- approximately 460 GB and 5.5 hours with igraph;
- approximately 310 GB and 2.75 hours with graph-tool;
- more than 500 GB attempted with NetworkX before the complete graph loaded.

Workstation analysis must therefore use columnar Parquet scans, out-of-core
aggregations, bounded batches, controlled sampling, and materialized
intermediate outputs.

## Initial Testable Hypotheses

1. Co-membership in a starter pack increases the probability of a follow edge
   relative to a random user pair.
2. Smaller packs have greater internal follow cohesion than larger packs.
3. Repeated co-membership across multiple packs increases follow probability
   more than a single co-membership.
4. The effect of pack membership on subsequent follows is moderated by pack
   age and account age.
5. Removing extremely frequent hub accounts reveals stronger alignment between
   communities in the two network representations.

