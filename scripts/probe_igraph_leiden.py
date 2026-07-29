from __future__ import annotations

import argparse
import gc
import os
import time

import igraph as ig
import leidenalg
import numpy as np
import psutil


def rss_gib() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / 1024**3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=100_000)
    parser.add_argument("--edges", type=int, default=5_000_000)
    parser.add_argument("--run-leiden", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    rng = np.random.default_rng(0)
    endpoints = np.empty((args.edges, 2), dtype=np.uint32)
    endpoints[:, 0] = rng.integers(0, args.nodes, args.edges, dtype=np.uint32)
    endpoints[:, 1] = rng.integers(0, args.nodes, args.edges, dtype=np.uint32)
    print(f"endpoints_ready rss_gib={rss_gib():.3f}", flush=True)
    if args.output:
        endpoints.tofile(args.output)
        print(f"endpoints_written path={args.output}", flush=True)

    started = time.perf_counter()
    graph = ig.Graph(n=args.nodes, edges=endpoints, directed=False)
    print(
        f"graph_ready seconds={time.perf_counter() - started:.3f} "
        f"rss_gib={rss_gib():.3f} v={graph.vcount()} e={graph.ecount()}",
        flush=True,
    )
    del endpoints
    gc.collect()
    print(f"input_released rss_gib={rss_gib():.3f}", flush=True)

    if args.run_leiden:
        started = time.perf_counter()
        partition = leidenalg.find_partition(
            graph,
            leidenalg.ModularityVertexPartition,
            seed=0,
        )
        print(
            f"leiden_ready seconds={time.perf_counter() - started:.3f} "
            f"rss_gib={rss_gib():.3f} communities={len(partition)} "
            f"modularity={partition.modularity:.8f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
