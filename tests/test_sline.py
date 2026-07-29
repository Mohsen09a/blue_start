from __future__ import annotations

import numpy as np

from blue_start.sline import _batch_paths, _merge_batches


def test_merge_sline_batches(tmp_path) -> None:
    first_hist, first_max, _ = _batch_paths(tmp_path, 0, 2)
    second_hist, second_max, _ = _batch_paths(tmp_path, 2, 4)
    np.asarray([0, 1, 1, 0], dtype=np.uint64).tofile(first_hist)
    np.asarray([2, 2, 0, 0], dtype=np.uint16).tofile(first_max)
    np.asarray([0, 0, 1, 0], dtype=np.uint64).tofile(second_hist)
    np.asarray([0, 1, 2, 2], dtype=np.uint16).tofile(second_max)

    histogram, maximum = _merge_batches(
        batch_root=tmp_path,
        pack_count=4,
        s_max=3,
        batch_packs=2,
    )

    np.testing.assert_array_equal(histogram, [0, 1, 2, 0])
    np.testing.assert_array_equal(maximum, [2, 2, 2, 2])
