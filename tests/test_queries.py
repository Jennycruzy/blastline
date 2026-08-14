from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from blastline.config import Settings
from blastline.ingest.pipeline import RegistryIngestor
from blastline.model import Edge, EdgeType, Node, NodeType, TimeInterval, package_id, repository_id, resolution_id, version_id
from blastline.query.engine import QueryEngine
from blastline.store import GraphStore


class QueryEngineTest(unittest.TestCase):
    def test_still_dirty_does_not_flag_repository_still_on_compromised_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GraphStore(Path(directory))
            start = datetime(2026, 7, 1, tzinfo=timezone.utc)
            end = datetime(2026, 7, 2, tzinfo=timezone.utc)
            repository = repository_id("github.com", "example/service")
            package = package_id("npm", "compromised")
            version = version_id("npm", "compromised", "1.0.0")
            resolution = resolution_id(repository, package, "1.0.0", start, "node_modules/compromised")
            store.add_nodes(
                [
                    Node(repository, NodeType.REPOSITORY, {"full_name": "example/service"}),
                    Node(package, NodeType.PACKAGE, {"registry": "npm", "name": "compromised"}),
                    Node(version, NodeType.VERSION, {"registry": "npm", "package": "compromised", "version": "1.0.0"}),
                    Node(resolution, NodeType.RESOLUTION, {"lock_path": "node_modules/compromised"}),
                ]
            )
            store.add_edges(
                [
                    Edge.create(resolution, EdgeType.RESOLVED_TO, version, TimeInterval(start, end), start),
                    Edge.create(repository, EdgeType.DECLARES, resolution, TimeInterval(start, end), start),
                ]
            )
            engine = QueryEngine(store, Settings.load(Path(__file__).resolve().parents[1]))
            response = engine.still_dirty("npm", "compromised", "1.0.0", (start, end), as_of=datetime(2026, 7, 1, 12, tzinfo=timezone.utc))
            self.assertEqual(response.results, ())
            self.assertEqual(response.coverage.resolvable_repositories, 1)

    def test_coverage_itemizes_repository_without_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GraphStore(Path(directory))
            repository = repository_id("github.com", "example/no-lockfile")
            store.add_nodes([Node(repository, NodeType.REPOSITORY, {"full_name": "example/no-lockfile"})])
            engine = QueryEngine(store, Settings.load(Path(__file__).resolve().parents[1]))
            response = engine.coverage_report()
            self.assertEqual(response.coverage.total_repositories, 1)
            self.assertEqual(response.coverage.resolvable_repositories, 0)
            self.assertEqual(response.coverage.unknown_repositories, 1)
            self.assertEqual(response.coverage.unknown_repository_ids, ("example/no-lockfile",))

    def test_window_query_uses_resolution_interval_and_differs_from_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GraphStore(Path(directory))
            start = datetime(2026, 7, 1, tzinfo=timezone.utc)
            end = datetime(2026, 7, 2, tzinfo=timezone.utc)
            window_end = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
            repo_id = repository_id("github.com", "example/service")
            package_node_id = package_id("npm", "compromised")
            bad_version_id = version_id("npm", "compromised", "1.0.0")
            app_version_id = version_id("npm", "service", "2.0.0")
            resolution_node_id = resolution_id(repo_id, package_id("npm", "service"), "2.0.0", start, "node_modules/service")
            compromised_resolution_id = resolution_id(repo_id, package_node_id, "1.0.0", start, "node_modules/compromised")
            store.add_nodes(
                [
                    Node(repo_id, NodeType.REPOSITORY, {"host": "github.com", "full_name": "example/service"}),
                    Node(package_node_id, NodeType.PACKAGE, {"registry": "npm", "name": "compromised"}),
                    Node(bad_version_id, NodeType.VERSION, {"registry": "npm", "package": "compromised", "version": "1.0.0"}),
                    Node(app_version_id, NodeType.VERSION, {"registry": "npm", "package": "service", "version": "2.0.0"}),
                    Node(resolution_node_id, NodeType.RESOLUTION, {"lock_path": "node_modules/service"}),
                    Node(compromised_resolution_id, NodeType.RESOLUTION, {"lock_path": "node_modules/compromised"}),
                ]
            )
            store.add_edges(
                [
                    Edge.create(app_version_id, EdgeType.DEPENDS_ON, package_node_id, TimeInterval(start), start),
                    Edge.create(resolution_node_id, EdgeType.RESOLVED_TO, app_version_id, TimeInterval(start, end), start),
                    Edge.create(repo_id, EdgeType.DECLARES, resolution_node_id, TimeInterval(start, end), start),
                    Edge.create(compromised_resolution_id, EdgeType.RESOLVED_TO, bad_version_id, TimeInterval(start, end), start),
                    Edge.create(repo_id, EdgeType.DECLARES, compromised_resolution_id, TimeInterval(start, end), start),
                ]
            )
            settings = Settings.load(Path(__file__).resolve().parents[1])
            engine = QueryEngine(store, settings)
            historical = engine.window_exposure("npm", "compromised", "1.0.0", (start, window_end), end)
            current = engine.current_exposure("npm", "compromised", "1.0.0", end)
            self.assertEqual({item["repository"] for item in historical.results}, {"example/service"})
            self.assertEqual(current.results, ())
            self.assertEqual(historical.coverage.resolvable_repositories, 1)

    def test_blast_radius_returns_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GraphStore(Path(directory))
            instant = datetime(2026, 7, 1, tzinfo=timezone.utc)
            repo_id = repository_id("github.com", "example/service")
            bad_package = package_id("npm", "compromised")
            bad_version = version_id("npm", "compromised", "1.0.0")
            app_version = version_id("npm", "service", "2.0.0")
            resolution_node = resolution_id(repo_id, package_id("npm", "service"), "2.0.0", instant, "node_modules/service")
            store.add_nodes(
                [
                    Node(repo_id, NodeType.REPOSITORY, {"full_name": "example/service"}),
                    Node(bad_package, NodeType.PACKAGE, {"registry": "npm", "name": "compromised"}),
                    Node(bad_version, NodeType.VERSION, {"registry": "npm", "package": "compromised", "version": "1.0.0"}),
                    Node(app_version, NodeType.VERSION, {"registry": "npm", "package": "service", "version": "2.0.0"}),
                    Node(resolution_node, NodeType.RESOLUTION, {"lock_path": "node_modules/service"}),
                ]
            )
            store.add_edges(
                [
                    Edge.create(app_version, EdgeType.DEPENDS_ON, bad_package, TimeInterval(instant), instant),
                    Edge.create(resolution_node, EdgeType.RESOLVED_TO, app_version, TimeInterval(instant), instant),
                    Edge.create(repo_id, EdgeType.DECLARES, resolution_node, TimeInterval(instant), instant),
                ]
            )
            engine = QueryEngine(store, Settings.load(Path(__file__).resolve().parents[1]))
            response = engine.blast_radius("npm", "compromised", "1.0.0", instant, instant)
            self.assertEqual(len(response.results), 1)
            self.assertEqual(response.results[0]["repository"], "example/service")
            self.assertIn("service@2.0.0", response.results[0]["path"])

    def test_unparseable_local_lockfile_is_unknown_in_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(root, Settings.load(Path(__file__).resolve().parents[1]).values)
            lockfile = root / "package-lock.json"
            lockfile.write_bytes(b"not json")
            ingestor = RegistryIngestor(settings)
            resolutions, issues, failures, _ = ingestor.local_lockfile(
                lockfile,
                "example/bad-lockfile",
                "npm",
                "2026-08-14T00:00:00Z",
                None,
            )
            self.assertEqual((resolutions, issues, failures), (0, 0, 1))
            coverage = QueryEngine(GraphStore(settings.path("graph", "directory")), settings).coverage_report()
            self.assertEqual(coverage.coverage.total_repositories, 1)
            self.assertEqual(coverage.coverage.resolvable_repositories, 0)
            self.assertEqual(coverage.coverage.unknown_repository_ids, ("example/bad-lockfile",))


if __name__ == "__main__":
    unittest.main()
