from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from blastline.config import Settings
from blastline.ingest.http import DiskHttpClient, HttpPolicy, HttpResponse
from blastline.ingest.snapshots import LockfileSnapshot, SnapshotLedger
from blastline.model import Edge, EdgeType, Node, NodeType, TimeInterval, package_id, repository_id, resolution_id, version_id
from blastline.store import GraphStore
from blastline.verify.grader import Verifier
from blastline.verify.lockfile_oracle import CachedLockfileOracle


ROOT = Path(__file__).resolve().parents[1]


def lockfile_body(package: str, version: str) -> bytes:
    return json.dumps(
        {
            "name": "example-service",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "example-service", "version": "1.0.0"},
                f"node_modules/{package}": {"version": version},
            },
        },
        sort_keys=True,
    ).encode("utf-8")


class LockfileOracleTest(unittest.TestCase):
    def test_snapshot_rejects_invalid_interval(self) -> None:
        start = datetime(2026, 8, 13, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "time interval end must be after start"):
            LockfileSnapshot.from_body(
                "example/service",
                "package-lock.json",
                "commit",
                "npm",
                start,
                start,
                "https://raw.example/service/commit/package-lock.json",
                lockfile_body("compromised", "1.0.0"),
            )

    def test_oracle_reads_cached_raw_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = lockfile_body("compromised", "1.0.0")
            url = "https://raw.example/service/commit/package-lock.json"
            http = DiskHttpClient(root / "cache", HttpPolicy(1, 1, 0.01), "blastline-test")
            http._write(http._path(http._key("GET", url, None)), HttpResponse(url, 200, {}, body, False))
            ledger = SnapshotLedger(root / "snapshots.jsonl")
            start = datetime(2026, 8, 13, tzinfo=timezone.utc)
            snapshot = LockfileSnapshot.from_body(
                "example/service",
                "package-lock.json",
                "commit",
                "npm",
                start,
                None,
                url,
                body,
            )
            self.assertTrue(ledger.record(snapshot))
            self.assertFalse(ledger.record(snapshot))

            observation = CachedLockfileOracle(ledger, http).observe(
                "npm",
                "compromised",
                "1.0.0",
                (start, datetime(2026, 8, 14, tzinfo=timezone.utc)),
                start,
            )

            self.assertEqual(observation.repositories, ("example/service",))
            self.assertEqual(observation.abstentions, ())

    def test_verifier_uses_raw_observation_instead_of_graph_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = datetime(2026, 8, 13, tzinfo=timezone.utc)
            end = datetime(2026, 8, 14, tzinfo=timezone.utc)
            graph_directory = root / "graph"
            cache_directory = root / "hydra" / "cache" / "http"
            ledger_path = root / "snapshots.jsonl"

            values = copy.deepcopy(Settings.load(ROOT).values)
            values["graph"]["directory"] = str(graph_directory)
            values["hydra"]["cache_directory"] = str(cache_directory)
            values["verification"]["lockfile_snapshot_ledger"] = str(ledger_path)
            settings = Settings(ROOT, values)

            graph_only_repository = repository_id("github.com", "graph/only")
            raw_repository = "raw/service"
            package = package_id("npm", "compromised")
            version = version_id("npm", "compromised", "1.0.0")
            advisory = "advisory:osv:TEST-1"
            resolution = resolution_id(graph_only_repository, package, "1.0.0", start, "node_modules/compromised")
            store = GraphStore(graph_directory)
            store.add_nodes(
                [
                    Node(graph_only_repository, NodeType.REPOSITORY, {"full_name": "graph/only"}),
                    Node(package, NodeType.PACKAGE, {"registry": "npm", "name": "compromised"}),
                    Node(version, NodeType.VERSION, {"registry": "npm", "package": "compromised", "version": "1.0.0"}),
                    Node(advisory, NodeType.ADVISORY, {"id": "TEST-1"}),
                    Node(resolution, NodeType.RESOLUTION, {"lock_path": "node_modules/compromised"}),
                ]
            )
            store.add_edges(
                [
                    Edge.create(advisory, EdgeType.AFFECTS, version, TimeInterval(start), start),
                    Edge.create(
                        resolution,
                        EdgeType.RESOLVED_TO,
                        version,
                        TimeInterval(start, end),
                        start,
                        {"evidence": "parsed-lockfile"},
                    ),
                    Edge.create(graph_only_repository, EdgeType.DECLARES, resolution, TimeInterval(start, end), start),
                ]
            )

            url = "https://raw.example/raw-service/commit/package-lock.json"
            body = lockfile_body("compromised", "1.0.0")
            http = DiskHttpClient(cache_directory.parent / "registry", HttpPolicy(1, 1, 0.01), "blastline-test")
            http._write(http._path(http._key("GET", url, None)), HttpResponse(url, 200, {}, body, False))
            SnapshotLedger(ledger_path).record(
                LockfileSnapshot.from_body(
                    raw_repository,
                    "package-lock.json",
                    "commit",
                    "npm",
                    start,
                    end,
                    url,
                    body,
                )
            )

            verifier = Verifier(store, settings)
            cases = verifier.discover_cases()
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].observed_repositories, (raw_repository,))
            self.assertEqual(cases[0].observation_abstentions, ())

            scorecard = verifier.grade()
            self.assertEqual(scorecard.gradable_cases, 1)
            self.assertEqual(scorecard.true_positives, 0)
            self.assertEqual(scorecard.false_positives, 1)
            self.assertEqual(scorecard.false_negatives, 1)


if __name__ == "__main__":
    unittest.main()
