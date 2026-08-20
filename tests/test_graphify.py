from __future__ import annotations

import unittest
from datetime import datetime, timezone

from blastline.ingest.graphify import graphify_package
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


if __name__ == "__main__":
    unittest.main()
