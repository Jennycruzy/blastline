from __future__ import annotations

import unittest
from pathlib import Path

from blastline.config import Settings
from blastline.query.engine import QueryEngine
from blastline.store import GraphStore
from ui.server import build_timeline_payload


ROOT = Path(__file__).resolve().parents[1]


class TimelineEndpointTest(unittest.TestCase):
    def test_timeline_payload_queries_real_graph_for_each_frame(self) -> None:
        settings = Settings.load(ROOT)
        engine = QueryEngine(GraphStore(settings.path("graph", "directory")), settings)
        body = build_timeline_payload(settings, engine)

        self.assertEqual(body["mode"], "live-temporal-query")
        self.assertEqual(len(body["frames"]), settings.integer("timeline", "frame_count"))
        exposed = [frame["exposed_repositories"] for frame in body["frames"]]
        self.assertEqual(exposed[0], [])
        self.assertEqual(exposed[-1], ["npm/cli"])
        self.assertTrue(any(frame["latency_ms"] >= 0 for frame in body["frames"]))


if __name__ == "__main__":
    unittest.main()
