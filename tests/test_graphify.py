from __future__ import annotations

import unittest
from datetime import datetime, timezone

from blastline.ingest.graphify import graphify_infrastructure, graphify_package
from blastline.ingest.records import RegistryPackage, RegistryVersion
from blastline.model import EdgeType, NodeType


class GraphifyMetadataTest(unittest.TestCase):
    def test_package_ingestion_emits_publisher_and_maintainer_relations(self) -> None:
        published = datetime(2026, 8, 1, tzinfo=timezone.utc)
        package = RegistryPackage(
            "npm",
            "example-package",
            (
                RegistryVersion(
                    registry="npm",
                    package_name="example-package",
                    version="1.0.0",
                    published_at=published,
                    modified_at=published,
                    maintainers=("alice",),
                    source_identifier="example-package@1.0.0",
                ),
            ),
            "example-package",
        )

        nodes, edges = graphify_package(package)

        self.assertIn(NodeType.MAINTAINER, {node.node_type for node in nodes})
        self.assertIn(NodeType.PUBLISH_INFRA, {node.node_type for node in nodes})
        self.assertEqual(
            {edge.edge_type for edge in edges},
            {
                EdgeType.PUBLISHED_FROM,
                EdgeType.PUBLISHED_THROUGH,
                EdgeType.PUBLISHED_BY,
                EdgeType.MAINTAINS,
            },
        )

    def test_infrastructure_fallback_does_not_claim_maintainer_attribution(self) -> None:
        published = datetime(2026, 8, 1, tzinfo=timezone.utc)

        edges = graphify_infrastructure(
            "npm",
            "example-package",
            (("1.0.0", published), ("2.0.0", published)),
            {"1.0.0"},
        )

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].edge_type, EdgeType.PUBLISHED_THROUGH)
        self.assertEqual(edges[0].source_id, "package:npm:example-package")
        self.assertEqual(edges[0].target_id, "publish-infra:npm")
        self.assertEqual(edges[0].metadata["source"], "graph-registry-identity")
        self.assertEqual(edges[0].metadata["version"], "2.0.0")


if __name__ == "__main__":
    unittest.main()
