# Time-Split Starter Pack Member Prediction

This is a complete, CPU-only graph-learning study that predicts the initial
non-creator membership of future Bluesky Starter Packs. It is isolated from the
paper-reproduction code and all other projects under `new_research/`.

The implementation uses the real local schema:

- timestamped user-to-user follows;
- timestamped Starter Packs and creators;
- timestamped Starter Pack memberships;
- account creation dates.

The supplied DuckDB does **not** contain posts, likes, reposts, replies, or a
general interaction table. Those prompt assumptions are therefore documented
as unavailable instead of silently fabricating interaction features.

## Final result

The untouched test period contains 7,689 usable future packs and 309,206
initial non-creator members. The hybrid graph model achieves:

| Metric | Popularity | Graph heuristic | Hypergraph cosine | Hybrid logistic |
|---|---:|---:|---:|---:|
| Hit@10 | 0.492 | 0.622 | 0.292 | **0.639** |
| MRR | 0.429 | 0.513 | 0.151 | **0.515** |
| Micro Recall@50 | 0.051 | 0.106 | 0.079 | **0.147** |
| Micro Recall@100 | 0.147 | 0.163 | 0.135 | **0.176** |

The hybrid model improves test micro Recall@100 by 8.35% relative to the graph
heuristic and 19.63% relative to popularity. Candidate retrieval is the main
bottleneck: the natural target-independent pool contains only 21.95% of all
future test members.

## Repository layout

```text
starterpack_prediction_time_split/
|-- code/
|   |-- data_loader.py      # DuckDB loading and strict chronological labels
|   |-- graph_builder.py    # pre-cutoff follow features and candidate retrieval
|   |-- embedder.py         # normalized hypergraph incidence and SVD embedding
|   |-- predictor.py        # streaming logistic ranker and ranking evaluation
|   |-- reporting.py        # final plots and concise report
|   `-- main.py             # end-to-end orchestration
|-- scripts/
|   |-- run_study.cmd
|   `-- run_tests.cmd
|-- tests/
|-- outputs/
|   |-- figures/
|   |-- parquet/
|   |-- summaries/
|   `-- models/             # ignored by Git
|-- docs/
|   `-- TECHNICAL_REPORT.md
|-- work/                   # large rebuildable state, ignored by Git
`-- config.toml
```

## Execution

Install the Python dependencies once, then run the commands from the repository
root. The launchers discover the repository path automatically:

```cmd
python -m pip install -r new_research\starterpack_prediction_time_split\requirements.txt
```

```cmd
new_research\starterpack_prediction_time_split\scripts\run_study.cmd
new_research\starterpack_prediction_time_split\scripts\run_tests.cmd
```

To rebuild every stage:

```cmd
new_research\starterpack_prediction_time_split\scripts\run_study.cmd --force
```

The launchers use `python` from `PATH`. To select a specific interpreter, set
`PYTHON_EXE` before running them, for example:

```cmd
set PYTHON_EXE=C:\Path\To\Python\python.exe
```

The initial full run scans the 2.416-billion-edge follow relation. Later runs
reuse the isolated DuckDB tables and saved embedding unless `--force` is used.

## Exact prediction task

For each future pack, the creator is known. The model ranks a natural pool of
historical candidate users. A positive label is a non-creator user whose
membership timestamp is on or before the pack creation date. Later additions
are excluded because they were not part of the pack's initial composition.

The candidate pool is constructed without consulting the target pack members.
It combines:

1. pre-cutoff direct follow neighbors of the creator;
2. users who shared a historical pack with the creator;
3. popular users in the creator's hypergraph embedding cluster;
4. globally popular historical pack members.

At most 512 candidates are retained for each target pack. Evaluation divides
top-K hits by **all** initial true members, not only true members present in the
candidate pool. The reported recall is therefore end-to-end.

## Strict chronological design

| Role | Period |
|---|---|
| Historical graph snapshot | 2024-06-01 through 2025-01-31 |
| Exclusion gap | 2025-02-01 through 2025-02-07 |
| Training labels | 2025-02-08 through 2025-05-31 |
| Exclusion gap | 2025-06-01 through 2025-06-07 |
| Validation labels | 2025-06-08 through 2025-07-31 |
| Exclusion gap | 2025-08-01 through 2025-08-07 |
| Test labels | 2025-08-08 through 2025-09-30 |

All graph, hypergraph, degree, direct-follow, co-membership, and popularity
features stop on 2025-01-31. This fixed basis is conservative: it becomes stale
for later packs, but it eliminates look-ahead leakage and avoids comparing SVD
coordinates learned in rotated latent bases.

The split implementation begins at
`code/data_loader.py:187`. Pre-cutoff follow features begin at
`code/graph_builder.py:14`, and candidate construction begins at
`code/graph_builder.py:214`.

# Mathematical prerequisites

This section defines every non-basic concept used by the primary pipeline.

## 1. Hypergraph

**Definition.** A hypergraph is a pair

$$
\mathcal{G}=(V,\mathcal{E}), \tag{1}
$$

where $V$ is a set of nodes and each hyperedge $e\in\mathcal{E}$ is an arbitrary
non-empty subset of $V$. Unlike an ordinary graph edge, a hyperedge may connect
more than two nodes.

**Intuition.** One Starter Pack naturally joins all of its members at once.
Replacing it by every pairwise edge would inflate a pack of size $n$ into
$n(n-1)/2$ edges and discard the identity of the original group.

**Use in code.** Historical users are nodes and historical Starter Packs are
hyperedges. The full incidence relation is built in `code/data_loader.py:187`.

## 2. Incidence matrix and degree matrices

**Definition.** For $|V|$ nodes and $|\mathcal{E}|$ hyperedges, the incidence
matrix is

$$
H_{ve}=\begin{cases}
1, & v\in e,\\
0, & v\notin e.
\end{cases} \tag{2}
$$

The node degree and hyperedge degree are

$$
d(v)=\sum_e H_{ve}, \qquad \delta(e)=\sum_v H_{ve}. \tag{3}
$$

Their diagonal matrices are

$$
D_v=\operatorname{diag}(d(v)), \qquad
D_e=\operatorname{diag}(\delta(e)). \tag{4}
$$

**Intuition.** $d(v)$ is the number of historical packs containing user $v$;
$\delta(e)$ is the size of pack $e$.

**Use in code.** `normalized_incidence()` at `code/embedder.py:31` computes
these degrees with sparse arrays.

## 3. Normalized hypergraph operator and Laplacian

The implementation forms

$$
B=D_v^{-1/2}H D_e^{-1/2}. \tag{5}
$$

For unit hyperedge weights, the associated normalized propagation matrix and
Laplacian are

$$
P=B B^T
=D_v^{-1/2}H D_e^{-1}H^T D_v^{-1/2}, \tag{6}
$$

$$
L=I-P. \tag{7}
$$

**Intuition.** The inverse square-root factors prevent very active users and
very large packs from dominating solely because they create more incidence
entries. $P$ measures two-step node-to-pack-to-node proximity.

**Use in code.** Equation (5) is constructed at `code/embedder.py:31`. The code
does not materialize $P$ or $L$, because either dense matrix would be far too
large. SVD of sparse $B$ obtains the same leading left singular subspace needed
for spectral node coordinates.

## 4. Truncated singular value decomposition

**Definition.** The singular value decomposition of $B$ is

$$
B=U\Sigma V^T. \tag{8}
$$

The rank-$k$ approximation keeps only the largest $k$ singular values:

$$
B_k=U_k\Sigma_kV_k^T. \tag{9}
$$

The node embedding used here is the L2-normalized row of $U_k\Sigma_k$.

**Eckart-Young-Mirsky theorem.** Among all matrices $X$ of rank at most $k$,
$B_k$ minimizes both the Frobenius error and spectral-norm error:

$$
B_k=\arg\min_{\operatorname{rank}(X)\le k}\|B-X\|_F. \tag{10}
$$

**Intuition.** The 32 retained dimensions are the strongest global patterns of
historical co-membership after degree and pack-size normalization.

**Use in code.** Randomized `TruncatedSVD` is fitted at
`code/embedder.py:121`; the surrounding pipeline begins at
`code/embedder.py:79`.

## 5. Cosine similarity

For two nonzero embedding vectors $z_u$ and $z_v$,

$$
\operatorname{cos}(u,v)
=\frac{z_u^Tz_v}{\|z_u\|_2\|z_v\|_2}. \tag{11}
$$

Because rows are pre-normalized, this reduces to $z_u^Tz_v$.

**Intuition.** Users with similar historical pack contexts have embeddings
pointing in similar directions.

**Use in code.** Cosine, absolute coordinate differences, and elementwise
products are constructed at `code/predictor.py:105`.

## 6. Logistic model and binary cross-entropy

For standardized feature vector $x$, weights $w$, and intercept $b$,

$$
\hat p=\sigma(w^Tx+b), \qquad
\sigma(a)=\frac{1}{1+e^{-a}}. \tag{12}
$$

For label $y\in\{0,1\}$, weighted binary cross-entropy with L2 regularization is

$$
\mathcal{L}(w,b)
=-\alpha_y\left[y\log \hat p +(1-y)\log(1-\hat p)\right]
+\frac{\lambda}{2}\|w\|_2^2. \tag{13}
$$

The vector gradient for one example is

$$
\nabla_w\mathcal{L}
=\alpha_y(\hat p-y)x+\lambda w, \qquad
\frac{\partial\mathcal{L}}{\partial b}=\alpha_y(\hat p-y). \tag{14}
$$

**Intuition.** A positive coefficient raises predicted membership odds when
that feature increases, holding other standardized features fixed. Positive
examples receive higher weight because natural candidates contain many more
negatives.

**Use in code.** Feature batching starts at `code/predictor.py:105`; scaling
and `SGDClassifier(loss="log_loss")` training begin at
`code/predictor.py:187` and `code/predictor.py:210`.

### Why this is not skip-gram negative sampling

No node2vec or skip-gram model is used. Therefore the node2vec transition
parameters $p,q$ and skip-gram noise-distribution loss are not part of this
implementation. The pipeline instead uses hypergraph SVD and supervised
per-pack negative sampling: all retrieved positives plus at most 128
deterministically selected negative candidates per training pack. Equation
(13), not the skip-gram loss, is the optimized objective.

## 7. Ranking metrics

Let $Y_e$ be all true initial non-creator members of pack $e$, and let
$R_e^K$ be the top-$K$ recommendations.

$$
\operatorname{Recall@K}(e)
=\frac{|Y_e\cap R_e^K|}{|Y_e|}. \tag{15}
$$

$$
\operatorname{Hit@K}(e)
=\mathbf{1}\{|Y_e\cap R_e^K|>0\}. \tag{16}
$$

If $r_e$ is the rank of the first true member, reciprocal rank is

$$
\operatorname{RR}(e)=\begin{cases}
1/r_e, & \text{a true member is retrieved},\\
0, & \text{otherwise}.
\end{cases} \tag{17}
$$

Mean reciprocal rank is the mean of (17) across packs. Macro Recall@K averages
(15) across packs; micro Recall@K divides the total number of hits by the total
number of true members. The denominator always includes cold-start members and
unretrieved eligible members.

**Use in code.** Evaluation begins at `code/predictor.py:328`.

## 8. Time-aware evaluation and look-ahead bias

**Definition.** Look-ahead bias occurs when a feature available only after a
prediction time influences model fitting or evaluation for that earlier time.

**Why the time split matters.** A random pack split would let the hypergraph
embedding include future target packs. Their true member co-occurrence would
then directly shape the representation being evaluated, inflating performance.

**Stationarity assumption.** The experiment assumes the relationship between
historical graph structure and future pack selection remains sufficiently
stable from February through September 2025 for a model trained on the earlier
period to rank later packs. The validation-to-test metric stability partially
supports this assumption, but does not prove it.

## Scalability and memory

| Object or stage | Actual size or control |
|---|---:|
| Historical users | 1,684,915 |
| Historical hyperedges | 296,957 |
| Historical incidence entries | 9,762,636 |
| Sparse normalized incidence | 80.9 MiB |
| Float32 node embeddings | 205.7 MiB |
| Embedding dimensions | 32 |
| Prediction candidate rows | 10,658,267 |
| Training rows after negative sampling | 4,063,128 |
| DuckDB RAM limit | 12 GB |
| DuckDB threads | 4 |
| Temporary disk cap | 70 GB |
| Model batch size | 200,000 rows |

A dense incidence matrix would require roughly
$1{,}684{,}915\times296{,}957\times4$ bytes, about 1.82 TiB even in float32.
The CSR matrix stores only the 9.76 million nonzero memberships and occupies
80.9 MiB. Follow edges remain external Parquet and are aggregated in DuckDB.

## Runtime benchmark

Measured on the local laptop during the completed run:

| Stage | Time |
|---|---:|
| Historical out-degree scan | 13.05 s |
| Historical in-degree scan | 123.40 s |
| Creator follow retrieval | 102.31 s |
| Hypergraph embedding and clustering | 36.39 s |
| Final candidate expansion | 7.66 s |
| Hybrid model training | 30.61 s |
| Validation and test ranking | 7.02 s |

The largest cost is scanning and joining the complete follow relation, not SVD
or logistic regression.

## Outputs

- `outputs/summaries/starterpack_prediction_summary.json`: full machine-readable result.
- `outputs/parquet/ranking_metrics.parquet`: validation and test metrics for all rankers.
- `outputs/parquet/per_pack_ranking_metrics.parquet`: pack-level audit metrics.
- `outputs/parquet/test_top100_recommendations.parquet`: ranked test recommendations.
- `outputs/parquet/model_coefficients.parquet`: standardized hybrid-model coefficients.
- `outputs/parquet/candidate_source_summary.parquet`: retrieval-source coverage.
- `outputs/figures/`: four final plots in PNG and PDF.
- `docs/TECHNICAL_REPORT.md`: concise six-section report.

## Limitations

- The fixed January representation is intentionally stale for later packs.
- About 19.8% of test members are absent from the historical hypergraph.
- Natural candidate retrieval reaches only 21.95% micro recall on test.
- Candidate sources overlap; source totals should not be added together.
- The model predicts deidentified users and is evaluated observationally.
- General interaction data requested by the prompt is unavailable locally.
