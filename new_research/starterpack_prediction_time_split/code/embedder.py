"""CPU-only spectral hypergraph embedding for historical Starter Packs."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import duckdb
import joblib
import numpy as np
import pyarrow as pa
from scipy import sparse
from sklearn.cluster import MiniBatchKMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize

from .data_loader import MODELS, STUDY_ROOT, StudyConfig
from .graph_builder import install_embedding_clusters


ARRAY_DIR = STUDY_ROOT / "work" / "arrays"
EMBEDDING_PATH = ARRAY_DIR / "hypergraph_embeddings_float32.npy"
CLUSTER_PATH = ARRAY_DIR / "hypergraph_clusters_uint16.npy"
MODEL_PATH = MODELS / "hypergraph_svd.joblib"
SUMMARY_PATH = STUDY_ROOT / "outputs" / "summaries" / "embedding_summary.json"


def normalized_incidence(
    node_indices: np.ndarray,
    pack_indices: np.ndarray,
    node_count: int,
    pack_count: int,
) -> sparse.csr_matrix:
    """Return D_v^-1/2 H D_e^-1/2 as a sparse CSR matrix.

    H has one non-zero for every historical node-pack membership. The two
    inverse square-root factors prevent high-degree users and large packs from
    dominating the singular vectors merely because they contain more entries.
    """
    node_degrees = np.bincount(node_indices, minlength=node_count).astype(np.float64)
    pack_degrees = np.bincount(pack_indices, minlength=pack_count).astype(np.float64)
    if np.any(node_degrees <= 0) or np.any(pack_degrees <= 0):
        raise ValueError("incidence matrix contains an empty node or hyperedge")
    values = 1.0 / np.sqrt(node_degrees[node_indices] * pack_degrees[pack_indices])
    matrix = sparse.coo_matrix(
        (values.astype(np.float32), (node_indices, pack_indices)),
        shape=(node_count, pack_count),
        dtype=np.float32,
    )
    return matrix.tocsr()


def _install_saved_clusters(
    connection: duckdb.DuckDBPyConnection,
    clusters: np.ndarray,
    *,
    force: bool,
) -> None:
    nodes = connection.execute(
        "SELECT node_id, node_index FROM meta.history_nodes ORDER BY node_index"
    ).fetchnumpy()
    node_ids = nodes["node_id"].astype(np.uint32, copy=False)
    node_indices = nodes["node_index"].astype(np.uint32, copy=False)
    if len(node_ids) != len(clusters):
        raise RuntimeError("saved cluster vector does not match history-node count")
    table = pa.table(
        {
            "node_id": pa.array(node_ids),
            "node_index": pa.array(node_indices),
            "cluster": pa.array(clusters.astype(np.uint16, copy=False)),
        }
    )
    install_embedding_clusters(connection, table, force=force)


def build_embeddings(
    connection: duckdb.DuckDBPyConnection,
    config: StudyConfig,
    *,
    force: bool,
) -> dict[str, Any]:
    """Fit or reload a normalized-incidence SVD and node clusters."""
    ARRAY_DIR.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if EMBEDDING_PATH.exists() and CLUSTER_PATH.exists() and MODEL_PATH.exists() and not force:
        embeddings = np.load(EMBEDDING_PATH, mmap_mode="r")
        clusters = np.load(CLUSTER_PATH, mmap_mode="r")
        _install_saved_clusters(connection, np.asarray(clusters), force=False)
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        print(f"[REUSE] hypergraph embeddings: {embeddings.shape}")
        return summary

    started = time.perf_counter()
    counts = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM meta.history_nodes),
            (SELECT count(*) FROM meta.history_packs),
            (SELECT count(*) FROM meta.hypergraph_incidence)
        """
    ).fetchone()
    node_count, pack_count, membership_count = (int(value) for value in counts)
    print(
        f"[EMBED] loading {membership_count:,} incidences for "
        f"{node_count:,} nodes and {pack_count:,} packs"
    )
    incidence = connection.execute(
        "SELECT node_index, pack_index FROM meta.hypergraph_incidence"
    ).fetchnumpy()
    node_indices = incidence["node_index"].astype(np.int64, copy=False)
    pack_indices = incidence["pack_index"].astype(np.int64, copy=False)
    matrix = normalized_incidence(node_indices, pack_indices, node_count, pack_count)
    del incidence, node_indices, pack_indices
    sparse_bytes = matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes
    print(f"[EMBED] normalized sparse matrix: {sparse_bytes / (1024**2):.1f} MiB")

    svd = TruncatedSVD(
        n_components=config.dimensions,
        n_iter=config.svd_iterations,
        random_state=config.seed,
        algorithm="randomized",
    )
    embeddings = svd.fit_transform(matrix).astype(np.float32, copy=False)
    # L2 normalization converts dot products into cosine similarity. Zero rows
    # cannot occur because every history node has at least one incidence.
    embeddings = normalize(embeddings, norm="l2", axis=1, copy=False)
    np.save(EMBEDDING_PATH, embeddings, allow_pickle=False)
    del matrix

    # Fit clusters on a deterministic, evenly spaced subset, then predict all
    # nodes in bounded batches. Clusters are used only for candidate retrieval.
    sample_size = min(500_000, node_count)
    sample_indices = np.linspace(0, node_count - 1, sample_size, dtype=np.int64)
    kmeans = MiniBatchKMeans(
        n_clusters=config.clusters,
        random_state=config.seed,
        batch_size=16_384,
        n_init=3,
        max_iter=100,
        reassignment_ratio=0.01,
    )
    kmeans.fit(embeddings[sample_indices])
    clusters = np.empty(node_count, dtype=np.uint16)
    for start in range(0, node_count, config.cluster_batch_rows):
        end = min(start + config.cluster_batch_rows, node_count)
        clusters[start:end] = kmeans.predict(embeddings[start:end]).astype(np.uint16)
    np.save(CLUSTER_PATH, clusters, allow_pickle=False)
    _install_saved_clusters(connection, clusters, force=True)

    joblib.dump(
        {
            "svd": svd,
            "kmeans": kmeans,
            "dimensions": config.dimensions,
            "normalization": "D_v^-1/2 H D_e^-1/2 followed by row L2",
        },
        MODEL_PATH,
        compress=3,
    )
    elapsed = time.perf_counter() - started
    summary = {
        "method": "normalized hypergraph incidence TruncatedSVD",
        "nodes": node_count,
        "hyperedges": pack_count,
        "memberships": membership_count,
        "dimensions": config.dimensions,
        "clusters": config.clusters,
        "explained_variance_ratio_sum": float(svd.explained_variance_ratio_.sum()),
        "sparse_matrix_mib": sparse_bytes / (1024**2),
        "embedding_file_mib": EMBEDDING_PATH.stat().st_size / (1024**2),
        "elapsed_seconds": elapsed,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[OK] hypergraph embeddings: {embeddings.shape} in {elapsed:.2f}s")
    return summary


def load_embeddings() -> np.ndarray:
    if not EMBEDDING_PATH.exists():
        raise FileNotFoundError("run the embedding stage first")
    return np.load(EMBEDDING_PATH, mmap_mode="r")
