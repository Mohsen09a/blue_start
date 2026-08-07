from __future__ import annotations

import numpy as np
import pyarrow as pa

from new_research.starterpack_prediction_time_split.code.embedder import normalized_incidence
from new_research.starterpack_prediction_time_split.code.predictor import (
    _numpy,
    _metric_accumulator,
    _update_pack_metrics,
    feature_names,
)


def test_normalized_incidence_values() -> None:
    # Node 0 belongs to two packs; nodes 1 and 2 belong to one. Pack 0 has
    # two nodes and pack 1 has three nodes.
    rows = np.asarray([0, 1, 0, 2], dtype=np.int64)
    cols = np.asarray([0, 0, 1, 1], dtype=np.int64)
    matrix = normalized_incidence(rows, cols, node_count=3, pack_count=2)
    assert matrix.shape == (3, 2)
    assert matrix.nnz == 4
    assert np.isclose(matrix[0, 0], 1 / np.sqrt(4))
    assert np.isclose(matrix[2, 1], 1 / np.sqrt(2))


def test_metric_is_end_to_end_against_all_true_members() -> None:
    labels = np.asarray([0, 1, 0], dtype=np.int8)
    scores = np.asarray([0.8, 0.7, 0.1], dtype=np.float32)
    candidate_ids = np.asarray([10, 11, 12], dtype=np.uint32)
    accumulator = _metric_accumulator((1, 2))
    row = _update_pack_metrics(
        accumulator,
        labels,
        scores,
        candidate_ids,
        total_positives=4,
        ks=(1, 2),
    )
    assert row["hit@1"] == 0
    assert row["hit@2"] == 1
    assert np.isclose(row["recall@2"], 0.25)
    assert np.isclose(row["candidate_recall"], 0.25)
    assert np.isclose(row["mrr"], 0.5)


def test_feature_name_count() -> None:
    # Two scalar embedding features, abs difference, product, and 13 graph
    # structural features.
    assert len(feature_names(32)) == 2 + 32 + 32 + 13


def test_nullable_unsigned_index_accepts_negative_sentinel() -> None:
    batch = pa.record_batch(
        [pa.array([1, None, 3], type=pa.uint32())], names=["creator_index"]
    )
    values = _numpy(batch, "creator_index", fill=-1)
    assert values.tolist() == [1, -1, 3]
