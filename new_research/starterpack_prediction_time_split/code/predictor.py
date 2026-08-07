"""Train graph-aware rankers and evaluate future Starter Pack composition."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import duckdb
import joblib
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler

from .data_loader import MODELS, PARQUET, SUMMARIES, StudyConfig
from .embedder import load_embeddings


MODEL_PATH = MODELS / "starterpack_member_logistic.joblib"
METRICS_PATH = PARQUET / "ranking_metrics.parquet"
PER_PACK_PATH = PARQUET / "per_pack_ranking_metrics.parquet"
RECOMMENDATIONS_PATH = PARQUET / "test_top100_recommendations.parquet"
COEFFICIENTS_PATH = PARQUET / "model_coefficients.parquet"
SUMMARY_PATH = SUMMARIES / "prediction_summary.json"


BASE_COLUMNS = (
    "pack_id",
    "creator_id",
    "candidate_id",
    "creator_index",
    "candidate_index",
    "creator_follows_candidate",
    "candidate_follows_creator",
    "shared_history_packs",
    "from_follow",
    "from_comember",
    "from_cluster",
    "from_global",
    "candidate_pack_count",
    "candidate_out_degree",
    "candidate_in_degree",
    "creator_pack_count",
    "creator_out_degree",
    "creator_in_degree",
    "retrieval_score",
    "label",
)


@dataclass
class BatchFeatures:
    x: np.ndarray
    labels: np.ndarray
    pack_ids: np.ndarray
    creator_ids: np.ndarray
    candidate_ids: np.ndarray
    heuristic_scores: np.ndarray
    popularity_scores: np.ndarray
    cosine_scores: np.ndarray


def feature_names(dimensions: int) -> list[str]:
    names = ["embedding_cosine", "creator_embedding_known"]
    names.extend(f"embedding_absdiff_{index}" for index in range(dimensions))
    names.extend(f"embedding_product_{index}" for index in range(dimensions))
    names.extend(
        [
            "creator_follows_candidate",
            "candidate_follows_creator",
            "log_shared_history_packs",
            "from_follow",
            "from_comember",
            "from_cluster",
            "from_global",
            "log_candidate_pack_count",
            "log_candidate_out_degree",
            "log_candidate_in_degree",
            "log_creator_pack_count",
            "log_creator_out_degree",
            "log_creator_in_degree",
        ]
    )
    return names


def _numpy(batch: pa.RecordBatch, name: str, *, fill: int | float = 0) -> np.ndarray:
    column = batch.column(batch.schema.get_field_index(name))
    if column.null_count:
        # Arrow cannot place a negative sentinel directly into an unsigned
        # integer column. Creator indices are nullable because future creators
        # may be absent from the historical hypergraph, so cast before filling.
        if fill < 0 and pa.types.is_unsigned_integer(column.type):
            column = pc.cast(column, pa.int64())
        column = pc.fill_null(column, fill)
    return column.to_numpy(zero_copy_only=False)


def make_features(batch: pa.RecordBatch, embeddings: np.ndarray) -> BatchFeatures:
    candidate_indices = _numpy(batch, "candidate_index").astype(np.int64, copy=False)
    creator_raw = _numpy(batch, "creator_index", fill=-1).astype(np.int64, copy=False)
    creator_known = creator_raw >= 0
    candidate_vectors = np.asarray(embeddings[candidate_indices], dtype=np.float32)
    creator_vectors = np.zeros_like(candidate_vectors)
    creator_vectors[creator_known] = embeddings[creator_raw[creator_known]]
    cosine = np.einsum("ij,ij->i", creator_vectors, candidate_vectors).astype(np.float32)
    absolute_difference = np.abs(creator_vectors - candidate_vectors)
    product = creator_vectors * candidate_vectors

    structural = np.column_stack(
        [
            _numpy(batch, "creator_follows_candidate").astype(np.float32),
            _numpy(batch, "candidate_follows_creator").astype(np.float32),
            np.log1p(_numpy(batch, "shared_history_packs").astype(np.float64)).astype(np.float32),
            _numpy(batch, "from_follow").astype(np.float32),
            _numpy(batch, "from_comember").astype(np.float32),
            _numpy(batch, "from_cluster").astype(np.float32),
            _numpy(batch, "from_global").astype(np.float32),
            np.log1p(_numpy(batch, "candidate_pack_count").astype(np.float64)).astype(np.float32),
            np.log1p(_numpy(batch, "candidate_out_degree").astype(np.float64)).astype(np.float32),
            np.log1p(_numpy(batch, "candidate_in_degree").astype(np.float64)).astype(np.float32),
            np.log1p(_numpy(batch, "creator_pack_count").astype(np.float64)).astype(np.float32),
            np.log1p(_numpy(batch, "creator_out_degree").astype(np.float64)).astype(np.float32),
            np.log1p(_numpy(batch, "creator_in_degree").astype(np.float64)).astype(np.float32),
        ]
    )
    x = np.column_stack(
        [
            cosine,
            creator_known.astype(np.float32),
            absolute_difference,
            product,
            structural,
        ]
    ).astype(np.float32, copy=False)
    popularity = (
        structural[:, 7]
        + 0.25 * structural[:, 9]
        + 0.10 * structural[:, 8]
    ).astype(np.float32)
    return BatchFeatures(
        x=x,
        labels=_numpy(batch, "label").astype(np.int8),
        pack_ids=_numpy(batch, "pack_id").astype(np.uint32, copy=False),
        creator_ids=_numpy(batch, "creator_id").astype(np.uint32, copy=False),
        candidate_ids=_numpy(batch, "candidate_id").astype(np.uint32, copy=False),
        heuristic_scores=_numpy(batch, "retrieval_score").astype(np.float32),
        popularity_scores=popularity,
        cosine_scores=cosine,
    )


def _training_query(config: StudyConfig) -> str:
    columns = ", ".join(BASE_COLUMNS)
    return f"""
        WITH ranked AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY pack_id, label
                    ORDER BY hash(pack_id, candidate_id, {config.seed})
                ) AS label_rank
            FROM results.prediction_candidates
            WHERE split='train'
        )
        SELECT {columns}
        FROM ranked
        WHERE label OR label_rank <= {config.training_negatives_per_pack}
        ORDER BY pack_id, candidate_id
    """


def _batch_reader(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    batch_rows: int,
) -> Iterator[pa.RecordBatch]:
    yield from connection.execute(query).fetch_record_batch(batch_rows)


def train_model(
    connection: duckdb.DuckDBPyConnection,
    config: StudyConfig,
    *,
    force: bool,
) -> dict[str, Any]:
    if MODEL_PATH.exists() and not force:
        payload = joblib.load(MODEL_PATH)
        print("[REUSE] trained hybrid logistic ranker")
        return payload["summary"]
    embeddings = load_embeddings()
    names = feature_names(embeddings.shape[1])
    query = _training_query(config)
    started = time.perf_counter()
    scaler = StandardScaler()
    rows = positives = 0
    for batch in _batch_reader(connection, query, config.model_batch_rows):
        features = make_features(batch, embeddings)
        scaler.partial_fit(features.x)
        rows += len(features.labels)
        positives += int(features.labels.sum())
    negatives = rows - positives
    positive_weight = min(20.0, negatives / max(positives, 1))
    model = SGDClassifier(
        loss="log_loss",
        penalty="l2",
        alpha=config.model_alpha,
        max_iter=1,
        tol=None,
        average=True,
        random_state=config.seed,
    )
    for epoch in range(config.model_epochs):
        batches = 0
        for batch in _batch_reader(connection, query, config.model_batch_rows):
            features = make_features(batch, embeddings)
            x = scaler.transform(features.x)
            weights = np.where(features.labels == 1, positive_weight, 1.0)
            model.partial_fit(
                x,
                features.labels,
                classes=np.asarray([0, 1], dtype=np.int8),
                sample_weight=weights,
            )
            batches += 1
        print(f"[MODEL] epoch {epoch + 1}/{config.model_epochs}: {batches} batches")
    coefficient_rows = [
        {
            "feature": name,
            "standardized_coefficient": float(value),
            "odds_ratio_per_sd": float(math.exp(np.clip(value, -30, 30))),
        }
        for name, value in zip(names, model.coef_[0], strict=True)
    ]
    pq.write_table(pa.Table.from_pylist(coefficient_rows), COEFFICIENTS_PATH, compression="zstd")
    elapsed = time.perf_counter() - started
    summary = {
        "training_rows": rows,
        "training_positives": positives,
        "training_prevalence": positives / max(rows, 1),
        "positive_sample_weight": positive_weight,
        "features": len(names),
        "epochs": config.model_epochs,
        "elapsed_seconds": elapsed,
    }
    joblib.dump(
        {
            "model": model,
            "scaler": scaler,
            "feature_names": names,
            "summary": summary,
        },
        MODEL_PATH,
        compress=3,
    )
    print(f"[OK] trained hybrid logistic ranker on {rows:,} rows in {elapsed:.2f}s")
    return summary


def _metric_accumulator(ks: tuple[int, ...]) -> dict[str, float]:
    values = {"packs": 0.0, "mrr": 0.0, "candidate_recall": 0.0}
    for k in ks:
        values[f"hit@{k}"] = 0.0
        values[f"macro_recall@{k}"] = 0.0
        values[f"micro_hits@{k}"] = 0.0
    values["total_positives"] = 0.0
    return values


def _update_pack_metrics(
    accumulator: dict[str, float],
    labels: np.ndarray,
    scores: np.ndarray,
    candidate_ids: np.ndarray,
    total_positives: int,
    ks: tuple[int, ...],
) -> dict[str, float]:
    order = np.lexsort((candidate_ids, -scores))
    ranked_labels = labels[order]
    positive_positions = np.flatnonzero(ranked_labels)
    accumulator["packs"] += 1
    accumulator["total_positives"] += total_positives
    accumulator["candidate_recall"] += float(labels.sum()) / max(total_positives, 1)
    accumulator["mrr"] += 0.0 if len(positive_positions) == 0 else 1.0 / (positive_positions[0] + 1)
    row = {
        "mrr": 0.0 if len(positive_positions) == 0 else 1.0 / (positive_positions[0] + 1),
        "candidate_recall": float(labels.sum()) / max(total_positives, 1),
    }
    for k in ks:
        hits = int(ranked_labels[:k].sum())
        accumulator[f"hit@{k}"] += float(hits > 0)
        accumulator[f"macro_recall@{k}"] += hits / max(total_positives, 1)
        accumulator[f"micro_hits@{k}"] += hits
        row[f"hit@{k}"] = float(hits > 0)
        row[f"recall@{k}"] = hits / max(total_positives, 1)
    return row


def _finalize_metrics(
    accumulator: dict[str, float],
    split: str,
    model_name: str,
    ks: tuple[int, ...],
) -> dict[str, Any]:
    packs = max(accumulator["packs"], 1.0)
    row: dict[str, Any] = {
        "split": split,
        "model": model_name,
        "packs": int(accumulator["packs"]),
        "mrr": accumulator["mrr"] / packs,
        "candidate_recall": accumulator["candidate_recall"] / packs,
    }
    for k in ks:
        row[f"hit_at_{k}"] = accumulator[f"hit@{k}"] / packs
        row[f"macro_recall_at_{k}"] = accumulator[f"macro_recall@{k}"] / packs
        row[f"micro_recall_at_{k}"] = accumulator[f"micro_hits@{k}"] / max(
            accumulator["total_positives"], 1.0
        )
    return row


def evaluate_split(
    connection: duckdb.DuckDBPyConnection,
    config: StudyConfig,
    split: str,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    embeddings = load_embeddings()
    model = payload["model"]
    scaler = payload["scaler"]
    columns = ", ".join(BASE_COLUMNS)
    query = f"""
        SELECT {columns}
        FROM results.prediction_candidates
        WHERE split='{split}'
        ORDER BY pack_id, candidate_id
    """
    totals = {
        int(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT pack_id, positive_members FROM meta.target_pack_totals WHERE split=?",
            [split],
        ).fetchall()
    }
    model_names = ("popularity", "graph_heuristic", "hypergraph_cosine", "hybrid_logistic")
    accumulators = {name: _metric_accumulator(config.evaluation_ks) for name in model_names}
    per_pack_rows: list[dict[str, Any]] = []
    recommendation_rows: list[dict[str, Any]] = []

    current_pack: int | None = None
    current_creator = 0
    buffered_ids: list[np.ndarray] = []
    buffered_labels: list[np.ndarray] = []
    buffered_scores: dict[str, list[np.ndarray]] = {name: [] for name in model_names}

    def flush() -> None:
        nonlocal current_pack, current_creator
        if current_pack is None:
            return
        candidate_ids = np.concatenate(buffered_ids)
        labels = np.concatenate(buffered_labels)
        scores = {name: np.concatenate(parts) for name, parts in buffered_scores.items()}
        total = totals[current_pack]
        for name in model_names:
            values = _update_pack_metrics(
                accumulators[name], labels, scores[name], candidate_ids, total, config.evaluation_ks
            )
            per_pack_rows.append(
                {
                    "split": split,
                    "model": name,
                    "pack_id": current_pack,
                    "positive_members": total,
                    **values,
                }
            )
        if split == "test":
            order = np.lexsort((candidate_ids, -scores["hybrid_logistic"]))[:100]
            for rank, position in enumerate(order, start=1):
                recommendation_rows.append(
                    {
                        "pack_id": current_pack,
                        "creator_id": current_creator,
                        "rank": rank,
                        "candidate_id": int(candidate_ids[position]),
                        "score": float(scores["hybrid_logistic"][position]),
                        "label": bool(labels[position]),
                    }
                )
        buffered_ids.clear()
        buffered_labels.clear()
        for parts in buffered_scores.values():
            parts.clear()

    for batch in _batch_reader(connection, query, config.model_batch_rows):
        features = make_features(batch, embeddings)
        learned = model.predict_proba(scaler.transform(features.x))[:, 1].astype(np.float32)
        batch_scores = {
            "popularity": features.popularity_scores,
            "graph_heuristic": features.heuristic_scores,
            "hypergraph_cosine": features.cosine_scores,
            "hybrid_logistic": learned,
        }
        starts = np.flatnonzero(
            np.r_[True, features.pack_ids[1:] != features.pack_ids[:-1]]
        )
        ends = np.r_[starts[1:], len(features.pack_ids)]
        for start, end in zip(starts, ends, strict=True):
            pack_id = int(features.pack_ids[start])
            if current_pack is not None and pack_id != current_pack:
                flush()
            if current_pack is None or pack_id != current_pack:
                current_pack = pack_id
                current_creator = int(features.creator_ids[start])
            buffered_ids.append(features.candidate_ids[start:end])
            buffered_labels.append(features.labels[start:end])
            for name in model_names:
                buffered_scores[name].append(batch_scores[name][start:end])
    flush()
    metric_rows = [
        _finalize_metrics(accumulators[name], split, name, config.evaluation_ks)
        for name in model_names
    ]
    return metric_rows, per_pack_rows, recommendation_rows


def evaluate_models(
    connection: duckdb.DuckDBPyConnection,
    config: StudyConfig,
    *,
    force: bool,
) -> dict[str, Any]:
    if METRICS_PATH.exists() and PER_PACK_PATH.exists() and SUMMARY_PATH.exists() and not force:
        print("[REUSE] ranking evaluation")
        return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    payload = joblib.load(MODEL_PATH)
    started = time.perf_counter()
    metrics: list[dict[str, Any]] = []
    per_pack: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    for split in ("validation", "test"):
        split_metrics, split_per_pack, split_recommendations = evaluate_split(
            connection, config, split, payload
        )
        metrics.extend(split_metrics)
        per_pack.extend(split_per_pack)
        recommendations.extend(split_recommendations)
        print(f"[EVAL] {split}: {len(split_per_pack) // 4:,} packs")
    pq.write_table(pa.Table.from_pylist(metrics), METRICS_PATH, compression="zstd")
    pq.write_table(pa.Table.from_pylist(per_pack), PER_PACK_PATH, compression="zstd")
    pq.write_table(
        pa.Table.from_pylist(recommendations), RECOMMENDATIONS_PATH, compression="zstd"
    )
    elapsed = time.perf_counter() - started
    summary = {"metrics": metrics, "elapsed_seconds": elapsed}
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[OK] ranking evaluation in {elapsed:.2f}s")
    return summary
