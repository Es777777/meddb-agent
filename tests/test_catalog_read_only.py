from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from meddb.catalog import Catalog


class CatalogReadOnlyTests(unittest.TestCase):
    def test_runtime_catalog_connection_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.sqlite"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE catalog_meta (key TEXT, value TEXT)")
                connection.execute("INSERT INTO catalog_meta VALUES ('version', 'test')")

            catalog = Catalog(path)
            self.assertEqual(catalog.metadata(), {"version": "test"})
            connection = catalog._connect()
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO catalog_meta VALUES ('mutated', 'no')")
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
