# Implementation Status

## Overall Result

All major computational analyses reported in the paper have been implemented
and executed using the released dataset on a 32 GB workstation.

The only optional exact-reproduction difference is the Leiden backend. The
full independent partition was computed locally with C/igraph, while the paper
used Python `leidenalg`. The original data-collection crawler and anonymization
pipeline are outside this repository; analysis begins with the published data.

## Full Dataset Foundation

- 39,650,447 accounts processed
- 365,842 Starter Packs processed
- 12,703,609 deduplicated hypergraph incidences
- 2,416,311,437 directed follow edges processed
- DuckDB configured with an 18 GB memory limit and eight threads
- large follow and projection relations kept on disk
- Parquet, JSON, PNG, and PDF outputs generated

## Completed Paper Analyses

| Analysis | Local status | Validation |
|---|---|---|
| Node statistics | Complete | Full dataset |
| Follow volume and degrees | Complete | 2.416 billion edges |
| Follow timestamp checks | Complete | Full dataset |
| Starter Pack statistics | Complete | Full dataset |
| Hypergraph components | Exact | Largest component 1,997,488 |
| Follow WCC | Exact | Official distribution exact match |
| Follow SCC | Exact | Official distribution exact match |
| Hypergraph k-core | Exact | Maximum core and `core >= 1000` match |
| Pair co-occurrence | Exact locally | Official distribution exact match |
| Weighted clique projection | Complete | 245,754,884 edges |
| Unrestricted s-line | Exact | All 345 official rows match |
| Independent Leiden | Complete | Full 245,669,033-edge giant graph |
| Edge entropy | Complete | Official and independent labels |
| Configuration model | Complete | Degrees and edge sizes preserved |
| Kendall tau | Complete | Top-one-million comparison |
| Research figures | Complete | PNG and PDF |

## Exact Full s-Line

The checkpointed native implementation computes thresholds `s=1..345`
without filtering or sampling.

```text
Active packs at s=1:             365,228
Distinct pack pairs at s=1: 19,559,507,901
Runtime:                          39.83 seconds
Checkpoint storage:              about 189 MB
Official differing rows:                  0
```

The 19.6-billion-edge line graph is not materialized. Compact CSR arrays,
reusable counters, and overlap histograms produce the exact summary safely.

## Full Independent Leiden

The complete unweighted giant projection was clustered locally:

```text
Nodes:                         1,997,488
Edges:                       245,669,033
Independent communities:              740
Modularity:                      0.661649
NMI with published labels:       0.873341
Adjusted Rand index:             0.843200
Observed process working set: about 8.2 GB
```

The paper reports 503 communities using a different backend. The exact
64-bit Python construction reached 95.9% total system RAM before Leiden
started, so the safe full run uses 32-bit C/igraph with the same objective and
main parameters.

## Exact Follow Components

Both complete calculations use disk-backed compact CSR/checkpoint techniques:

```text
Largest WCC: 36,433,172
Largest SCC: 20,495,220
```

Both component-size distributions exactly match the official results.

## Entropy and Configuration Model

Using the independent local Leiden labels:

```text
Fully labeled packs:                 365,157
Observed mean normalized entropy:   0.138833
Randomized mean normalized entropy: 0.563722
Shuffle attempts:                   3,651,570
```

Every node degree and every hyperedge size is preserved.

The earlier official-label recomputation remains available separately and
reproduces the paper's means of approximately `0.160` observed and `0.576`
randomized.

## Follow Query Indexes

The full follow relation has disk-partitioned source and destination indexes:

- outgoing follows;
- incoming followers;
- edge-existence checks;
- mutual follows;
- common follows.

The indexes occupy approximately 20.28 GiB locally and are excluded from Git.

## Tests

Thirteen automated tests pass. The native s-line kernel also passes a separate
brute-force toy-graph validation.

## Remaining Work

No major paper analysis remains unimplemented.

Optional or thesis-extension work includes:

- exact Leiden rerun with the paper's backend on an HPC system;
- reproducing the external data collection and anonymization pipeline;
- novel link-prediction, temporal, or causal analysis;
- final thesis writing and presentation preparation.

