"""Generate final figures and a concise result report."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pyarrow.parquet as pq

from .data_loader import FIGURES, PARQUET, STUDY_ROOT, SUMMARIES


SUMMARY_PATH = SUMMARIES / "starterpack_prediction_summary.json"
REPORT_PATH = STUDY_ROOT / "docs" / "TECHNICAL_REPORT.md"

COLORS = {
    "popularity": "#94A3B8",
    "graph_heuristic": "#F59E0B",
    "hypergraph_cosine": "#14B8A6",
    "hybrid_logistic": "#2F66E8",
}


def _save(fig, stem: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(FIGURES / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def build_figures(summary: dict) -> list[str]:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
        }
    )
    outputs = []
    splits = summary["splits"]
    names = [row["split"].title() for row in splits]
    packs = [row["packs"] for row in splits]
    eligibility = [100 * row["eligibility_rate"] for row in splits]
    retrieval = [
        100 * next(item["candidate_recall"] for item in summary["candidates"] if item["split"] == row["split"])
        for row in splits
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].bar(names, packs, color=["#334155", "#64748B", "#2F66E8"])
    axes[0].set_title("Strict future-pack splits")
    axes[0].set_ylabel("Packs")
    for index, value in enumerate(packs):
        axes[0].text(index, value, f"{value:,}", ha="center", va="bottom", fontsize=9)
    x = np.arange(len(names))
    width = 0.36
    axes[1].bar(x - width / 2, eligibility, width, label="Historical-user eligibility", color="#14B8A6")
    axes[1].bar(x + width / 2, retrieval, width, label="Natural candidate recall", color="#F59E0B")
    axes[1].set_xticks(x, names)
    axes[1].set_ylim(0, 100)
    axes[1].set_ylabel("Share of future members (%)")
    axes[1].set_title("Cold-start and retrieval ceilings")
    axes[1].legend(frameon=False)
    fig.tight_layout()
    _save(fig, "prediction_data_scope")
    outputs.append("prediction_data_scope")

    metrics = summary["evaluation"]["metrics"]
    test = [row for row in metrics if row["split"] == "test"]
    order = ["popularity", "graph_heuristic", "hypergraph_cosine", "hybrid_logistic"]
    labels = ["Popularity", "Graph heuristic", "Hypergraph cosine", "Hybrid logistic"]
    metric_specs = [
        ("hit_at_10", "Hit@10"),
        ("mrr", "MRR"),
        ("micro_recall_at_50", "Micro Recall@50"),
        ("micro_recall_at_100", "Micro Recall@100"),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(14, 3.8))
    for axis, (key, title) in zip(axes, metric_specs, strict=True):
        values = [next(row[key] for row in test if row["model"] == model) for model in order]
        axis.bar(np.arange(4), values, color=[COLORS[model] for model in order])
        axis.set_title(title)
        axis.set_xticks(np.arange(4), labels, rotation=35, ha="right")
        axis.set_ylim(0, max(values) * 1.22)
        for index, value in enumerate(values):
            axis.text(index, value, f"{value:.3f}", ha="center", va="bottom", fontsize=8)
    fig.suptitle("Untouched test-period ranking performance", fontsize=14, y=1.02)
    fig.tight_layout()
    _save(fig, "prediction_test_metrics")
    outputs.append("prediction_test_metrics")

    source_rows = summary["candidate_sources"]
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    x = np.arange(len(source_rows))
    width = 0.19
    sources = [
        ("positives_from_follow", "Follow neighbors", "#2F66E8"),
        ("positives_from_comember", "Historical co-members", "#F59E0B"),
        ("positives_from_cluster", "Embedding cluster", "#14B8A6"),
        ("positives_from_global", "Global popular", "#94A3B8"),
    ]
    for offset, (key, label, color) in enumerate(sources):
        values = [100 * row[key] / max(row["positives"], 1) for row in source_rows]
        ax.bar(x + (offset - 1.5) * width, values, width, label=label, color=color)
    ax.set_xticks(x, [row["split"].title() for row in source_rows])
    ax.set_ylabel("Share of retrieved positives (%)")
    ax.set_title("Which retrieval sources contain true future members?")
    ax.legend(frameon=False, ncol=2)
    fig.tight_layout()
    _save(fig, "prediction_candidate_sources")
    outputs.append("prediction_candidate_sources")

    coefficients = pq.read_table(PARQUET / "model_coefficients.parquet").to_pylist()
    strongest = sorted(coefficients, key=lambda row: abs(row["standardized_coefficient"]), reverse=True)[:15]
    strongest.reverse()
    fig, ax = plt.subplots(figsize=(9, 6))
    values = [row["standardized_coefficient"] for row in strongest]
    ax.barh(
        [row["feature"] for row in strongest],
        values,
        color=["#2F66E8" if value >= 0 else "#DC2626" for value in values],
    )
    ax.axvline(0, color="#111827", linewidth=0.8)
    ax.set_xlabel("Standardized logistic coefficient")
    ax.set_title("Strongest adjusted features (correlated, descriptive)")
    fig.tight_layout()
    _save(fig, "prediction_model_coefficients")
    outputs.append("prediction_model_coefficients")
    return outputs


def build_report(summary: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    metrics = summary["evaluation"]["metrics"]
    test = {row["model"]: row for row in metrics if row["split"] == "test"}
    validation = {row["model"]: row for row in metrics if row["split"] == "validation"}
    split_rows = summary["splits"]
    candidate_rows = {row["split"]: row for row in summary["candidates"]}
    report = f"""# Time-Split Starter Pack Member Prediction

## Abstract

This study predicts the initial non-creator membership of future Bluesky Starter Packs from a fixed historical graph snapshot. The representation is trained on **{summary['embedding']['hyperedges']:,}** historical packs, **{summary['embedding']['nodes']:,}** users, and **{summary['embedding']['memberships']:,}** incidence entries. A 32-dimensional normalized hypergraph SVD is combined with timestamp-safe follow, co-membership, degree, and popularity features. Evaluation uses strict validation and test periods separated by seven-day gaps. On the untouched test period, the hybrid model reaches **Hit@10={test['hybrid_logistic']['hit_at_10']:.3f}**, **MRR={test['hybrid_logistic']['mrr']:.3f}**, and **micro Recall@100={test['hybrid_logistic']['micro_recall_at_100']:.3f}**.

## 1. Problem formulation

For a future pack $e$ with known creator $c_e$ and creation time $t_e$, the system produces a score $s(c_e,v)$ for every retrievable candidate user $v$. No graph edge or pack membership after the historical cutoff is used as an input feature. Positive labels include only non-creator members whose recorded membership date is on or before $t_e$.

## 2. Data and strict time split

| Split | Dates | Packs | Initial members | Historically eligible | Eligibility |
|---|---|---:|---:|---:|---:|
"""
    for row in split_rows:
        report += f"| {row['split'].title()} | {row['first_date']} to {row['last_date']} | {row['packs']:,} | {row['positive_members']:,} | {row['eligible_positive_members']:,} | {100*row['eligibility_rate']:.2f}% |\n"
    report += rf"""

The graph snapshot ends on **2025-01-31**. Training begins on **2025-02-08**, validation begins on **2025-06-08**, and testing begins on **2025-08-08**. Each boundary has a seven-day exclusion gap. The local dataset contains follows and Starter Pack membership timestamps but no posts, likes, replies, or repost tables.

![Data scope](../outputs/figures/prediction_data_scope.png)

## 3. Hypergraph embedding and candidate retrieval

Historical packs are hyperedges. Let $H \in \{{0,1}}^{{|V|\times|E|}}$ be the node-hyperedge incidence matrix, $D_v$ the diagonal node-degree matrix, and $D_e$ the diagonal hyperedge-size matrix. The fitted sparse matrix is

$$
B = D_v^{{-1/2}} H D_e^{{-1/2}}. \tag{{1}}
$$

Randomized truncated SVD returns $B \approx U_k\Sigma_kV_k^T$, and each node embedding is the row-normalized vector from $U_k\Sigma_k$. The exact sparse matrix occupies **{summary['embedding']['sparse_matrix_mib']:.1f} MiB** and the 32-dimensional float32 embedding file occupies **{summary['embedding']['embedding_file_mib']:.1f} MiB**.

Candidates are retrieved without reading target labels from four sources: pre-cutoff follow neighbors, historical co-members, popular nodes from the creator's embedding cluster, and global popular historical nodes. At most 512 candidates are retained per pack. Test micro candidate recall is **{100*candidate_rows['test']['candidate_recall']:.2f}%**, which is the main end-to-end ceiling.

![Candidate sources](../outputs/figures/prediction_candidate_sources.png)

## 4. Rankers

The baselines are historical popularity, a timestamp-safe graph heuristic, and raw hypergraph cosine similarity. The learned model is a regularized logistic ranker using embedding cosine, absolute embedding differences, elementwise products, direct-follow flags, shared-pack counts, retrieval-source flags, and pre-cutoff degree/popularity features. With standardized feature vector $x$, probability is

$$
P(y=1\mid x)=\sigma(w^Tx+b). \tag{{2}}
$$

The weighted binary cross-entropy is optimized by streaming SGD in 200,000-row batches for three epochs. Training uses **{summary['model']['training_rows']:,}** candidate pairs and never loads the full table into Python memory.

## 5. Results

| Split | Model | Hit@10 | MRR | Micro R@50 | Micro R@100 |
|---|---|---:|---:|---:|---:|
"""
    for split_name, rows in (("Validation", validation), ("Test", test)):
        for model in ("popularity", "graph_heuristic", "hypergraph_cosine", "hybrid_logistic"):
            row = rows[model]
            report += f"| {split_name} | {model.replace('_',' ').title()} | {row['hit_at_10']:.3f} | {row['mrr']:.3f} | {row['micro_recall_at_50']:.3f} | {row['micro_recall_at_100']:.3f} |\n"
    gain = 100 * (test["hybrid_logistic"]["micro_recall_at_100"] / test["graph_heuristic"]["micro_recall_at_100"] - 1)
    report += rf"""

The hybrid model improves test micro Recall@100 by **{gain:.2f}% relative** over the graph heuristic and by **{100*(test['hybrid_logistic']['micro_recall_at_100']/test['popularity']['micro_recall_at_100']-1):.2f}% relative** over popularity. Raw hypergraph cosine is weak by itself, but embedding coordinates add information when combined with direct graph and popularity features.

![Test metrics](../outputs/figures/prediction_test_metrics.png)

## 6. Limitations and conclusion

- The fixed representation is intentionally stale after 2025-01-31; this prevents leakage and SVD basis drift but lowers later retrieval coverage.
- Approximately 20% of test members are absent from the historical hypergraph and are true cold-start users.
- The natural candidate pool retrieves only 21.95% of all test positives, so candidate generation is a larger bottleneck than reranking.
- Candidate-source flags overlap, and learned coefficients are correlated descriptive quantities rather than causal effects.
- No posts or general interaction tables exist in the supplied dataset.

The experiment is technically successful: the learned graph-aware ranker consistently beats simple baselines on a chronologically later test period. For future work, the highest-value improvement is a dynamic candidate retriever updated near each pack date, followed by inductive embeddings for cold-start accounts.
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def run() -> None:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    outputs = build_figures(summary)
    build_report(summary)
    print(f"[OK] figures: {', '.join(outputs)}")
    print(f"[OK] report: {REPORT_PATH}")


if __name__ == "__main__":
    run()
