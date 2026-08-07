"""Run the complete time-split Starter Pack prediction pipeline."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .data_loader import (
    PARQUET,
    SUMMARIES,
    build_temporal_tables,
    connect,
    load_config,
    split_summary,
)
from .embedder import build_embeddings
from .graph_builder import (
    build_candidates,
    build_history_graph_features,
    candidate_summary,
)
from .predictor import evaluate_models, train_model


def export_auxiliary_tables(connection) -> dict[str, Any]:
    source_rows = connection.execute(
        """
        SELECT
            split,
            count(*)::UBIGINT AS candidates,
            sum(label::INTEGER)::UBIGINT AS positives,
            sum(from_follow::INTEGER)::UBIGINT AS from_follow,
            sum(from_comember::INTEGER)::UBIGINT AS from_comember,
            sum(from_cluster::INTEGER)::UBIGINT AS from_cluster,
            sum(from_global::INTEGER)::UBIGINT AS from_global,
            sum(label::INTEGER) FILTER (WHERE from_follow)::UBIGINT AS positives_from_follow,
            sum(label::INTEGER) FILTER (WHERE from_comember)::UBIGINT AS positives_from_comember,
            sum(label::INTEGER) FILTER (WHERE from_cluster)::UBIGINT AS positives_from_cluster,
            sum(label::INTEGER) FILTER (WHERE from_global)::UBIGINT AS positives_from_global
        FROM results.prediction_candidates
        GROUP BY split
        ORDER BY CASE split WHEN 'train' THEN 1 WHEN 'validation' THEN 2 ELSE 3 END
        """
    ).fetch_arrow_table()
    pq.write_table(source_rows, PARQUET / "candidate_source_summary.parquet", compression="zstd")
    split_rows = connection.execute(
        """
        SELECT
            split,
            count(*)::UBIGINT AS packs,
            sum(positive_members)::UBIGINT AS positive_members,
            sum(eligible_positive_members)::UBIGINT AS eligible_positive_members,
            avg(positive_members)::DOUBLE AS mean_initial_noncreator_members,
            median(positive_members)::DOUBLE AS median_initial_noncreator_members,
            min(date_created)::DATE AS first_date,
            max(date_created)::DATE AS last_date
        FROM meta.target_pack_totals
        GROUP BY split
        ORDER BY CASE split WHEN 'train' THEN 1 WHEN 'validation' THEN 2 ELSE 3 END
        """
    ).fetch_arrow_table()
    pq.write_table(split_rows, PARQUET / "time_split_summary.parquet", compression="zstd")
    stages = connection.execute(
        "SELECT stage, elapsed_seconds, row_count, details FROM meta.stages ORDER BY completed_at"
    ).fetchall()
    return {
        "candidate_sources": source_rows.to_pylist(),
        "stages": [
            {
                "stage": row[0],
                "elapsed_seconds": float(row[1]),
                "row_count": int(row[2]),
                "details": row[3],
            }
            for row in stages
        ],
    }


def run(*, force: bool = False) -> dict[str, Any]:
    config = load_config()
    started = time.perf_counter()
    connection = connect(config)
    try:
        build_temporal_tables(connection, config, force=force)
        build_history_graph_features(connection, config, force=force)
        embedding = build_embeddings(connection, config, force=force)
        build_candidates(connection, config, force=force)
        model = train_model(connection, config, force=force)
        evaluation = evaluate_models(connection, config, force=force)
        auxiliary = export_auxiliary_tables(connection)
        summary = {
            "study": "Time-Split Starter Pack Member Prediction",
            "completed": True,
            "task": "rank initial non-creator members of future Starter Packs from a fixed historical graph snapshot",
            "configuration": config.__dict__,
            "data_availability": {
                "used": ["timestamped follows", "timestamped Starter Packs", "timestamped memberships", "account creation dates"],
                "unavailable": ["posts", "likes", "reposts", "replies", "general interaction table"],
            },
            "leakage_controls": [
                "hypergraph and follow features stop at 2025-01-31",
                "training labels start after a seven-day gap on 2025-02-08",
                "validation starts after a seven-day train gap on 2025-06-08",
                "test starts after a seven-day validation gap on 2025-08-08",
                "only members present on or before pack creation are positive labels",
                "candidate retrieval never reads target-pack membership labels",
            ],
            "splits": split_summary(connection),
            "candidates": candidate_summary(connection),
            "embedding": embedding,
            "model": model,
            "evaluation": evaluation,
            "candidate_sources": auxiliary["candidate_sources"],
            "stages": auxiliary["stages"],
            "runtime_seconds_current_invocation": time.perf_counter() - started,
            "limitations": [
                "The fixed 2025-01-31 representation is intentionally stale for later packs but prevents representation leakage and SVD-basis drift.",
                "Ranking is restricted to a target-independent pool of at most 512 retrievable historical users per pack.",
                "Members absent from the historical hypergraph are cold-start users and cannot be recommended by the primary model.",
                "Evaluation is observational and does not imply that a recommendation would cause inclusion.",
                "No post or interaction tables exist in the supplied local dataset.",
            ],
        }
        SUMMARIES.mkdir(parents=True, exist_ok=True)
        (SUMMARIES / "starterpack_prediction_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        return summary
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="rebuild every stage")
    args = parser.parse_args()
    summary = run(force=args.force)
    print(json.dumps({"completed": summary["completed"], "runtime_seconds": summary["runtime_seconds_current_invocation"]}, indent=2))


if __name__ == "__main__":
    main()
