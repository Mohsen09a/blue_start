# Initial Project Plan

## Interpretation of the Paper

The paper releases an aligned, anonymized dataset with two relationship types:

1. a directed pairwise follow network;
2. a higher-order network of starter packs modeled as a hypergraph.

Anonymous user identifiers are consistent across both networks. This enables
direct analysis of the relationship between group membership and pairwise edge
formation.

Key values reported in the paper:

- 39,650,447 accounts;
- 36,447,725 nodes and 2,416,311,437 edges in the follow network;
- 2,003,536 starter-pack members and 365,842 starter packs;
- low-to-moderate centrality-rank correlation between the two network
  representations, indicating complementary information.

## Proposed Core Research Question

**Can starter-pack group structure explain or predict the existence and
formation of follow relationships?**

This question follows directly from the research gap identified in the paper
and supports several meaningful extensions.

## Phase 0: Data Understanding and Validation

- Record file sizes, names, and schemas.
- Sample and validate dates and identifiers.
- Document available hardware.
- Produce a deterministic, manageable development sample.

Deliverable: a data-quality report and a fixed development dataset.

## Phase 1: Reproduce Baseline Results

- Starter-pack size distribution.
- Number of pack memberships per user.
- Streaming in-degree and out-degree distributions.
- Coverage of starter-pack members in the follow network.
- Reproduction of the centrality-rank comparison from Figure 8.

Deliverable: reference figures demonstrating that the processing pipeline is
correct.

## Phase 2: Main Analysis

For each pair of users sharing a starter pack, investigate:

- whether a follow relationship exists;
- the direction of the relationship;
- whether the relationship was formed before or after pack membership;
- how pack size, age, and overlap affect the relationship.

To prevent pair explosion, pairs must be generated in batches. Very large packs
require controlled sampling or an explicit size threshold.

## Phase 3: Novel Extensions

Three suitable directions are:

1. **Link prediction:** use higher-order features to predict follow edges.
2. **Temporal analysis:** estimate how starter-pack membership changes the
   probability of a subsequent follow.
3. **Community alignment:** compare follow-network communities with hypergraph
   communities using entropy and overlap measures.

The final direction should be selected according to the thesis requirements
and available hardware.

## Technical Principles

- Never load the complete follow dataset with pandas or NetworkX.
- Use DuckDB scans over Parquet for large tables.
- Checkpoint expensive computations and store intermediate results as Parquet.
- Use fixed random seeds for every sampled analysis.
- Treat raw data as read-only and write generated artifacts under `outputs/`.
- Follow the dataset's ethical condition and never attempt to re-identify users.

## Decisions for the Next Research Meeting

- Is the thesis focused on reproduction, statistical analysis, or machine
  learning?
- Is analysis of the complete population required, or is a representative
  sample acceptable?
- What evaluation metric will define success?
- Which outputs are required by the supervisor: code, figures, a report, a
  model, or all of them?

