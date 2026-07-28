import tempfile
import unittest
from pathlib import Path

from blue_start.paths import DatasetSpec, resolve_dataset


class ResolveDatasetTests(unittest.TestCase):
    def test_resolve_canonical_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "example.csv.gz"
            expected.touch()
            self.assertEqual(resolve_dataset(DatasetSpec("example", expected.name), root), expected)

    def test_resolve_windows_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = root / "example (1).parquet"
            duplicate.touch()
            spec = DatasetSpec("example", "example.parquet")
            self.assertEqual(resolve_dataset(spec, root), duplicate)

    def test_resolve_duplicate_inserted_before_last_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = root / "example.json(1).gz"
            duplicate.touch()
            spec = DatasetSpec("example", "example.json.gz")
            self.assertEqual(resolve_dataset(spec, root), duplicate)


if __name__ == "__main__":
    unittest.main()
