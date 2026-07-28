import unittest
from pathlib import Path

from blue_start.duckdb_backend import sql_path
from blue_start.settings import load_settings


class ConfigurationTests(unittest.TestCase):
    def test_default_settings_are_workstation_safe(self) -> None:
        settings = load_settings()
        self.assertEqual(settings.memory_limit, "18GB")
        self.assertEqual(settings.threads, 8)
        self.assertFalse(settings.preserve_insertion_order)
        self.assertTrue(str(settings.temp_directory).endswith("work\\duckdb_tmp"))

    def test_sql_path_escapes_quotes_and_uses_forward_slashes(self) -> None:
        value = sql_path(Path("folder") / "it's.parquet")
        self.assertTrue(value.startswith("'"))
        self.assertIn("it''s.parquet", value)
        self.assertNotIn("\\", value)


if __name__ == "__main__":
    unittest.main()

