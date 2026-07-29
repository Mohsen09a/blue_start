from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    executable = project / "work" / "sline_full" / "sline_full.exe"
    if not executable.exists():
        raise FileNotFoundError("Run s-line-full once to build the native executable")

    hyperedges = ({0, 1, 2}, {1, 2, 3}, {2, 3}, {4})
    edge_offsets = np.zeros(len(hyperedges) + 1, dtype=np.int64)
    edge_nodes = np.asarray(
        [node for edge in hyperedges for node in sorted(edge)],
        dtype=np.uint32,
    )
    edge_offsets[1:] = np.cumsum([len(edge) for edge in hyperedges])
    node_count = 5
    node_memberships = [
        [pack for pack, edge in enumerate(hyperedges) if node in edge]
        for node in range(node_count)
    ]
    node_offsets = np.zeros(node_count + 1, dtype=np.int64)
    node_edges = np.asarray(
        [pack for memberships in node_memberships for pack in memberships],
        dtype=np.uint32,
    )
    node_offsets[1:] = np.cumsum([len(value) for value in node_memberships])

    with tempfile.TemporaryDirectory(prefix="blue_start_sline_") as temporary:
        root = Path(temporary)
        inputs = {
            "edge_offsets": edge_offsets,
            "edge_nodes": edge_nodes,
            "node_offsets": node_offsets,
            "node_edges": node_edges,
        }
        paths: dict[str, Path] = {}
        for name, values in inputs.items():
            path = root / f"{name}.bin"
            values.tofile(path)
            paths[name] = path
        histogram_path = root / "histogram.bin"
        maximum_path = root / "maximum.bin"
        completed = subprocess.run(
            [
                str(executable),
                str(paths["edge_offsets"]),
                str(paths["edge_nodes"]),
                str(paths["node_offsets"]),
                str(paths["node_edges"]),
                str(len(hyperedges)),
                str(node_count),
                str(len(edge_nodes)),
                "0",
                str(len(hyperedges)),
                "3",
                str(histogram_path),
                str(maximum_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        histogram = np.fromfile(histogram_path, dtype=np.uint64)
        maximum = np.fromfile(maximum_path, dtype=np.uint16)

    # Exact-overlap bins: one pair overlaps once and two overlap twice.
    np.testing.assert_array_equal(histogram, np.asarray([0, 1, 2, 0]))
    np.testing.assert_array_equal(maximum, np.asarray([2, 2, 2, 0]))
    print(completed.stdout.rstrip())
    print("[OK] Native unrestricted s-line matches the brute-force toy graph")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
