from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from blastline.model import Edge, EdgeType, Node, NodeType, TimeInterval
from blastline.store import GraphStore


class BitemporalGraphTest(unittest.TestCase):
    def test_append_only_update_preserves_historical_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GraphStore(Path(directory))
            start = datetime(2026, 8, 13, 9, tzinfo=timezone.utc)
            update = datetime(2026, 8, 13, 9, 5, tzinfo=timezone.utc)
            valid_query = datetime(2026, 8, 13, 9, 6, tzinfo=timezone.utc)
            repo = Node("repository:test:payments", NodeType.REPOSITORY)
            bad = Node("version:npm:demo@1.0.0", NodeType.VERSION)
            clean = Node("version:npm:demo@1.0.1", NodeType.VERSION)
            store.add_nodes([repo, bad, clean])
            store.add_edges(
                [
                    Edge.create(repo.node_id, EdgeType.RESOLVED_TO, bad.node_id, TimeInterval(start, update), start),
                    Edge.create(repo.node_id, EdgeType.RESOLVED_TO, clean.node_id, TimeInterval(update, None), update),
                ]
            )
            before_disclosure = store.outgoing(repo.node_id, EdgeType.RESOLVED_TO, valid_query, start)
            after_disclosure = store.outgoing(repo.node_id, EdgeType.RESOLVED_TO, valid_query, update)
            self.assertEqual(before_disclosure, [])
            self.assertEqual([edge.target_id for edge in after_disclosure], [clean.node_id])
            self.assertEqual(len(store.edges()), 2)

    def test_reingest_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GraphStore(Path(directory))
            node = Node("package:npm:demo", NodeType.PACKAGE, {"name": "demo"})
            edge = Edge.create(
                node.node_id,
                EdgeType.MAINTAINS,
                "maintainer:alice",
                TimeInterval(datetime(2026, 8, 13, tzinfo=timezone.utc)),
                datetime(2026, 8, 13, tzinfo=timezone.utc),
            )
            first = (store.add_nodes([node]), store.add_edges([edge]), store.fingerprint())
            second = (store.add_nodes([node]), store.add_edges([edge]), store.fingerprint())
            self.assertEqual(first[2], second[2])
            self.assertEqual(second[:2], (0, 0))


if __name__ == "__main__":
    unittest.main()
