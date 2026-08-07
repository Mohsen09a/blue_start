"""Independent integrity checks for the completed prediction study."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pyarrow.parquet as pq
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[1]
DATABASE = ROOT / "work" / "starterpack_prediction.duckdb"
SOURCE_DATABASE = PROJECT_ROOT / "work" / "blue_start.duckdb"
OUTPUTS = ROOT / "outputs"
SUMMARY = OUTPUTS / "summaries" / "starterpack_prediction_summary.json"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def main() -> None:
    check(DATABASE.exists(), "isolated study database exists")
    check(SUMMARY.exists(), "machine-readable summary exists")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    check(summary["completed"] is True, "summary marks the run complete")
    connection = duckdb.connect(str(DATABASE), read_only=True)
    try:
        connection.execute(f"ATTACH '{SOURCE_DATABASE.as_posix()}' AS source (READ_ONLY)")
        split_dates = connection.execute(
            """
            SELECT split, min(date_created), max(date_created), count(*)
            FROM meta.target_pack_totals GROUP BY split ORDER BY split
            """
        ).fetchall()
        date_map = {row[0]: (str(row[1]), str(row[2]), int(row[3])) for row in split_dates}
        check(date_map["train"][:2] == ("2025-02-08", "2025-05-31"), "training dates are strict")
        check(date_map["validation"][:2] == ("2025-06-08", "2025-07-31"), "validation dates are strict")
        check(date_map["test"][:2] == ("2025-08-08", "2025-09-30"), "test dates are strict")
        overlap = connection.execute(
            """
            SELECT count(*) - count(DISTINCT pack_id) FROM meta.target_pack_totals
            """
        ).fetchone()[0]
        check(overlap == 0, "target packs occur in exactly one split")
        labels_after_creation = connection.execute(
            """
            SELECT count(*)
            FROM meta.target_memberships AS t
            JOIN meta.target_packs AS p USING (pack_id)
            WHERE NOT EXISTS (
                SELECT 1
                FROM source.main.starterpack_memberships AS m
                WHERE m.pack_id=t.pack_id
                  AND m.member_id=t.member_id
                  AND m.date_added <= p.date_created
            )
            """
        ).fetchone()[0]
        check(labels_after_creation == 0, "positive labels are present by pack creation")
        max_candidates, duplicate_rows, self_candidates = connection.execute(
            """
            SELECT
                max(candidate_count),
                sum(row_count - distinct_count),
                sum(self_count)
            FROM (
                SELECT
                    pack_id,
                    count(*) AS candidate_count,
                    count(*) AS row_count,
                    count(DISTINCT candidate_id) AS distinct_count,
                    count(*) FILTER (WHERE creator_id=candidate_id) AS self_count
                FROM results.prediction_candidates
                GROUP BY pack_id
            )
            """
        ).fetchone()
        check(max_candidates <= 512, "candidate cap is respected")
        check(duplicate_rows == 0, "candidate IDs are unique within every pack")
        check(self_candidates == 0, "creators are excluded from candidates")
        label_mismatches = connection.execute(
            """
            SELECT count(*)
            FROM results.prediction_candidates AS c
            LEFT JOIN meta.target_memberships AS m
              ON m.pack_id=c.pack_id AND m.member_id=c.candidate_id
            WHERE c.label <> (m.member_id IS NOT NULL)
            """
        ).fetchone()[0]
        check(label_mismatches == 0, "candidate labels exactly match initial memberships")
        node_count, pack_count, incidence_count = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM meta.history_nodes),
              (SELECT count(*) FROM meta.history_packs),
              (SELECT count(*) FROM meta.hypergraph_incidence)
            """
        ).fetchone()
        check(node_count == 1_684_915, "full historical node population is present")
        check(pack_count == 296_957, "full historical hyperedge population is present")
        check(incidence_count == 9_762_636, "full historical incidence population is present")
    finally:
        connection.close()

    embeddings = np.load(ROOT / "work" / "arrays" / "hypergraph_embeddings_float32.npy", mmap_mode="r")
    check(embeddings.shape == (1_684_915, 32), "embedding array has the expected full shape")
    check(embeddings.dtype == np.float32, "embedding array uses memory-safe float32")

    metrics = pq.read_table(OUTPUTS / "parquet" / "ranking_metrics.parquet").to_pylist()
    check(len(metrics) == 8, "all four rankers are evaluated on validation and test")
    for row in metrics:
        for key, value in row.items():
            if key in {"split", "model", "packs"}:
                continue
            check(0 <= value <= 1, f"{row['split']} {row['model']} {key} is valid")
    test = {row["model"]: row for row in metrics if row["split"] == "test"}
    check(test["hybrid_logistic"]["hit_at_10"] > test["graph_heuristic"]["hit_at_10"], "hybrid Hit@10 beats graph heuristic on test")
    check(test["hybrid_logistic"]["micro_recall_at_100"] > test["graph_heuristic"]["micro_recall_at_100"], "hybrid micro Recall@100 beats graph heuristic on test")
    check(test["hybrid_logistic"]["micro_recall_at_100"] > test["popularity"]["micro_recall_at_100"], "hybrid micro Recall@100 beats popularity on test")

    figures = list((OUTPUTS / "figures").glob("*.png"))
    check(len(figures) == 4, "all four final PNG figures exist")
    check((ROOT / "README.md").exists(), "complete mathematical README exists")
    check((ROOT / "docs" / "TECHNICAL_REPORT.md").exists(), "editable technical report exists")
    pdf = ROOT / "output" / "pdf" / "time_split_starterpack_prediction_report.pdf"
    check(pdf.exists(), "six-page PDF report exists")
    check(len(PdfReader(str(pdf)).pages) == 6, "PDF respects the six-page limit")
    print("[DONE] every prediction-study validation check passed")


if __name__ == "__main__":
    main()
