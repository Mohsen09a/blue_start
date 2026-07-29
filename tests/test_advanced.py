import unittest

import numpy as np

from blue_start.advanced import (
    _build_node_to_edge_csr,
    compress_seen_paths,
    hypergraph_core_numbers,
    normalized_edge_entropies,
    random_edge_shuffles,
    sample_large_pack_pairs,
    union_edge_batch,
)
from blue_start.scc import (
    fill_csr_batch,
    kosaraju_component_sizes,
    kosaraju_finishing_order,
)


class AdvancedAlgorithmTests(unittest.TestCase):
    def test_union_find_components(self) -> None:
        parent = np.arange(6, dtype=np.uint32)
        sizes = np.ones(6, dtype=np.uint32)
        seen = np.zeros(6, dtype=np.uint8)
        sources = np.array([0, 1, 3], dtype=np.uint32)
        destinations = np.array([1, 2, 4], dtype=np.uint32)

        union_edge_batch(parent, sizes, seen, sources, destinations)
        compress_seen_paths(parent, seen)

        roots = parent[np.flatnonzero(seen)]
        component_sizes = np.bincount(roots)
        self.assertEqual(sorted(component_sizes[component_sizes > 0]), [2, 3])

    def test_compact_hypergraph_kcore(self) -> None:
        # Edges: {0,1,2}, {1,2}, {2,3}, {3}
        edge_offsets = np.array([0, 3, 5, 7, 8], dtype=np.int64)
        edge_nodes = np.array([0, 1, 2, 1, 2, 2, 3, 3], dtype=np.uint32)
        node_offsets, node_edges = _build_node_to_edge_csr(
            edge_offsets,
            edge_nodes,
            4,
        )

        core = hypergraph_core_numbers(
            edge_offsets,
            edge_nodes,
            node_offsets,
            node_edges,
        )

        np.testing.assert_array_equal(core, np.array([1, 2, 1, 1]))

    def test_normalized_entropy(self) -> None:
        edge_offsets = np.array([0, 4, 6], dtype=np.int64)
        edge_nodes = np.array([0, 1, 2, 3, 0, 1], dtype=np.uint32)
        communities = np.array([0, 0, 1, 1], dtype=np.int32)

        values = normalized_edge_entropies(
            edge_offsets,
            edge_nodes,
            communities,
            2,
        )

        self.assertAlmostEqual(values[0], 0.5)
        self.assertEqual(values[1], 0.0)

    def test_random_shuffle_preserves_degrees_and_edge_sizes(self) -> None:
        edge_offsets = np.array([0, 3, 5, 8], dtype=np.int64)
        edge_nodes = np.array([0, 1, 2, 2, 3, 1, 3, 4], dtype=np.uint32)
        original_sizes = np.diff(edge_offsets).copy()
        original_degrees = np.bincount(edge_nodes, minlength=5)

        random_edge_shuffles(
            edge_offsets,
            edge_nodes,
            node_count=5,
            attempts=100,
            seed=7,
        )

        np.testing.assert_array_equal(np.diff(edge_offsets), original_sizes)
        np.testing.assert_array_equal(
            np.bincount(edge_nodes, minlength=5),
            original_degrees,
        )
        for edge in range(len(edge_offsets) - 1):
            members = edge_nodes[edge_offsets[edge] : edge_offsets[edge + 1]]
            self.assertEqual(len(members), len(np.unique(members)))

    def test_large_pack_pair_sampling_and_scaling(self) -> None:
        import random

        members = np.array([1, 2, 3, 4], dtype=np.uint32)
        exact_pairs, exact_weight = sample_large_pack_pairs(
            members,
            sample_size=10,
            rng=random.Random(0),
        )
        self.assertEqual(len(exact_pairs), 6)
        self.assertEqual(exact_weight, 1.0)

        sampled_pairs, sampled_weight = sample_large_pack_pairs(
            members,
            sample_size=3,
            rng=random.Random(0),
        )
        self.assertEqual(len(sampled_pairs), 3)
        self.assertEqual(len(set(sampled_pairs)), 3)
        self.assertEqual(sampled_weight, 2.0)

    def test_iterative_kosaraju_scc(self) -> None:
        # 0 <-> 1, 1 -> 2, 2 <-> 3, and 4 is not part of the edge graph.
        forward_offsets = np.array([0, 1, 3, 4, 5, 5], dtype=np.uint64)
        forward_neighbors = np.array([1, 0, 2, 3, 2], dtype=np.uint32)
        reverse_offsets = np.array([0, 1, 2, 4, 5, 5], dtype=np.uint64)
        reverse_neighbors = np.array([1, 0, 1, 3, 2], dtype=np.uint32)
        visited = np.zeros(5, dtype=np.uint8)
        order = np.empty(4, dtype=np.uint32)
        stack_nodes = np.empty(4, dtype=np.uint32)
        stack_positions = np.empty(4, dtype=np.uint64)

        order_count = kosaraju_finishing_order(
            forward_offsets,
            forward_neighbors,
            reverse_offsets,
            visited,
            order,
            stack_nodes,
            stack_positions,
        )
        self.assertEqual(order_count, 4)

        visited.fill(0)
        sizes = np.empty(4, dtype=np.uint32)
        component_count = kosaraju_component_sizes(
            reverse_offsets,
            reverse_neighbors,
            order,
            order_count,
            visited,
            stack_nodes,
            sizes,
        )
        self.assertEqual(component_count, 2)
        self.assertEqual(sorted(sizes[:component_count]), [2, 2])

    def test_csr_fill_retry_resets_current_bucket(self) -> None:
        offsets = np.array([0, 2, 3], dtype=np.uint64)
        cursors = offsets[:-1].copy()
        neighbors = np.zeros(3, dtype=np.uint32)
        marker = np.zeros(2, dtype=np.uint16)
        keys = np.array([0, 1, 0], dtype=np.uint32)
        values = np.array([4, 5, 6], dtype=np.uint32)

        overflow = fill_csr_batch(
            keys,
            values,
            offsets,
            cursors,
            neighbors,
            marker,
            1,
        )
        self.assertEqual(overflow, 0)
        np.testing.assert_array_equal(neighbors, np.array([4, 6, 5]))

        # A retried bucket uses a fresh marker and overwrites the same slots.
        marker.fill(0)
        retry_values = np.array([7, 8, 9], dtype=np.uint32)
        overflow = fill_csr_batch(
            keys,
            retry_values,
            offsets,
            cursors,
            neighbors,
            marker,
            1,
        )
        self.assertEqual(overflow, 0)
        np.testing.assert_array_equal(neighbors, np.array([7, 9, 8]))


if __name__ == "__main__":
    unittest.main()
