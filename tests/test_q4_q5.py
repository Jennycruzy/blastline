from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from blastline.config import Settings
from blastline.model import Edge, EdgeType, Node, NodeType, TimeInterval, package_id, repository_id, resolution_id, version_id
from blastline.query.engine import QueryEngine
from blastline.store import GraphStore


class Q4Q5QueryTest(unittest.TestCase):
    def test_q4_returns_repository_blast_radius_for_valid_maintainer_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            instant = datetime(2026, 8, 1, tzinfo=timezone.utc)
            store = GraphStore(Path(directory))
            maintainer = "maintainer:npm:alice"
            package = package_id("npm", "shared-package")
            dependent = version_id("npm", "application", "2.0.0")
            repository = repository_id("github.com", "example/app")
            resolution = resolution_id(repository, package, "1.0.0", instant, "node_modules/shared-package")
            store.add_nodes(
                [
                    Node(maintainer, NodeType.MAINTAINER, {"name": "alice", "registry": "npm"}),
                    Node(package, NodeType.PACKAGE, {"name": "shared-package", "registry": "npm"}),
                    Node(dependent, NodeType.VERSION, {"package": "application", "registry": "npm", "version": "2.0.0"}),
                    Node(repository, NodeType.REPOSITORY, {"full_name": "example/app"}),
                    Node(resolution, NodeType.RESOLUTION, {"lock_path": "node_modules/shared-package"}),
                ]
            )
            store.add_edges(
                [
                    Edge.create(maintainer, EdgeType.MAINTAINS, package, TimeInterval(instant), instant),
                    Edge.create(dependent, EdgeType.DEPENDS_ON, package, TimeInterval(instant), instant),
                    Edge.create(resolution, EdgeType.RESOLVED_TO, dependent, TimeInterval(instant), instant),
                    Edge.create(repository, EdgeType.DECLARES, resolution, TimeInterval(instant), instant),
                ]
            )

            engine = QueryEngine(store, Settings.load(Path(__file__).resolve().parents[1]))
            response = engine.maintainer_risk("alice", valid_at=instant)

            self.assertEqual(response.abstentions, ())
            self.assertEqual(response.results[0]["package"], "shared-package")
            self.assertEqual(response.results[0]["transitive_repositories"], ["example/app"])

    def test_q5_uses_package_published_through_relation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            instant = datetime(2026, 8, 1, tzinfo=timezone.utc)
            store = GraphStore(Path(directory))
            infra = "publish-infra:npm"
            target_package = package_id("npm", "target")
            other_package = package_id("npm", "other")
            target_version = version_id("npm", "target", "1.0.0")
            other_version = version_id("npm", "other", "2.0.0")
            store.add_nodes(
                [
                    Node(infra, NodeType.PUBLISH_INFRA, {"registry": "npm"}),
                    Node(target_package, NodeType.PACKAGE, {"name": "target", "registry": "npm"}),
                    Node(other_package, NodeType.PACKAGE, {"name": "other", "registry": "npm"}),
                    Node(target_version, NodeType.VERSION, {"package": "target", "registry": "npm", "version": "1.0.0"}),
                    Node(other_version, NodeType.VERSION, {"package": "other", "registry": "npm", "version": "2.0.0"}),
                ]
            )
            store.add_edges(
                [
                    Edge.create(target_version, EdgeType.PUBLISHED_FROM, infra, TimeInterval(instant), instant),
                    Edge.create(other_version, EdgeType.PUBLISHED_FROM, infra, TimeInterval(instant), instant),
                    Edge.create(target_package, EdgeType.PUBLISHED_THROUGH, infra, TimeInterval(instant), instant),
                    Edge.create(other_package, EdgeType.PUBLISHED_THROUGH, infra, TimeInterval(instant), instant),
                ]
            )

            engine = QueryEngine(store, Settings.load(Path(__file__).resolve().parents[1]))
            response = engine.shared_infrastructure("npm", "target", "1.0.0", valid_at=instant)

            self.assertEqual(response.abstentions, ())
            self.assertIn(
                {
                    "shared_by": "PUBLISHED_THROUGH",
                    "shared_identifier": infra,
                    "package": "other",
                    "versions": ["2.0.0"],
                    "version_count": 1,
                },
                response.results,
            )


if __name__ == "__main__":
    unittest.main()
