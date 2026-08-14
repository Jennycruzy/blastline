from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from blastline.ingest.parsers import parse_npm, parse_pypi
from blastline.ingest.sources import read_index_checkpoint, write_index_checkpoint


ROOT = Path(__file__).resolve().parents[1]


class RecordedRegistryResponseTest(unittest.TestCase):
    def test_recorded_npm_response_is_real_and_parseable(self) -> None:
        path = ROOT / "cache" / "recordings" / "npm" / "lodash.json"
        self.assertTrue(path.exists(), f"missing recording: run scripts/record_real_responses.py --npm-package lodash")
        package, issues = parse_npm(path.read_bytes(), str(path))
        self.assertEqual(package.registry, "npm")
        self.assertEqual(package.name, "lodash")
        self.assertGreater(len(package.versions), 0)
        self.assertIsInstance(issues, tuple)

    def test_pypi_catalog_checkpoint_resumes_only_same_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pypi.json"
            write_index_checkpoint(path, "pypi", "catalog-a", 7)
            self.assertEqual(read_index_checkpoint(path, "pypi", "catalog-a", 10), 7)
            self.assertEqual(read_index_checkpoint(path, "pypi", "catalog-b", 10), 0)

    def test_recorded_pypi_response_is_real_and_parseable(self) -> None:
        path = ROOT / "cache" / "recordings" / "pypi" / "requests.json"
        self.assertTrue(path.exists(), f"missing recording: run scripts/record_real_responses.py --pypi-package requests")
        package, issues = parse_pypi(path.read_bytes(), str(path))
        self.assertEqual(package.registry, "pypi")
        self.assertEqual(package.name, "requests")
        self.assertGreater(len(package.versions), 0)
        self.assertIsInstance(issues, tuple)


if __name__ == "__main__":
    unittest.main()
