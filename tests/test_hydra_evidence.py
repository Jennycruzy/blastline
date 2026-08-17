from __future__ import annotations

import unittest

from blastline.query.hydra_evidence import (
    hydra_query,
    local_id,
    parse_candidate_paths,
    parse_relations,
    parse_typed_edge_metadata,
    response_source_ids,
)
from blastline.timeutil import parse_time


class HydraEvidenceParserTest(unittest.TestCase):
    def test_parses_documented_graph_context_path_and_chunk_sources(self) -> None:
        body = {
            "chunks": [
                {"source_id": "blastline:edge:resolved"},
                {"source_id": "blastline:edge:declared"},
            ],
            "graph_context": {
                "query_paths": [
                    {
                        "triplets": [
                            {
                                "source": {"id": "edge:declared", "name": "Repository"},
                                "relation": {"canonical_predicate": "DECLARES"},
                                "target": {"id": "edge:resolved", "name": "Resolution"},
                            }
                        ],
                        "relevancy_score": 0.9,
                        "group_id": "p_0",
                        "source_chunk_ids": ["blastline:edge:declared", "blastline:edge:resolved"],
                    }
                ]
            },
        }
        paths = parse_candidate_paths(body)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].relations[0].predicate, "DECLARES")
        self.assertEqual(response_source_ids(body), ("blastline:edge:resolved", "blastline:edge:declared"))
        self.assertEqual(local_id("blastline:edge:resolved"), "edge:resolved")

    def test_requires_temporal_identity_metadata_for_edge_chunks(self) -> None:
        body = {
            "chunks": [
                {
                    "source_id": "blastline:edge:resolved",
                    "additional_metadata": {
                        "blastline_record_type": "edge",
                        "edge_id": "edge:resolved",
                        "source_id": "resolution:one",
                        "target_id": "version:npm:lodash@4.17.21",
                        "valid_start": "2026-07-08T18:00:00Z",
                        "valid_end": "2026-07-08T20:30:00Z",
                        "commit_at": "2026-07-08T18:05:00Z",
                        "graph_fingerprint": "fingerprint",
                    },
                }
            ]
        }
        metadata = parse_typed_edge_metadata(body)
        self.assertEqual(metadata["blastline:edge:resolved"]["target_id"], "version:npm:lodash@4.17.21")

    def test_parses_structured_relation_endpoint(self) -> None:
        relations = parse_relations(
            {
                "relations": [
                    {
                        "source": {"id": "edge:resolved", "name": "Resolution"},
                        "relation": {"canonical_predicate": "RESOLVED_TO"},
                        "target": {"id": "version:npm:lodash@4.17.21", "name": "lodash@4.17.21"},
                    }
                ]
            }
        )
        self.assertEqual(relations[0].target.entity_id, "version:npm:lodash@4.17.21")
        self.assertEqual(relations[0].predicate, "RESOLVED_TO")

    def test_query_is_deterministic_and_contains_temporal_axes(self) -> None:
        valid_at = parse_time("2026-07-08T19:00:00Z")
        known_at = parse_time("2026-07-08T20:30:00Z")
        window = (valid_at, parse_time("2026-07-08T20:30:00Z"))
        first = hydra_query("npm", "lodash", "4.17.21", window, known_at)
        second = hydra_query("npm", "lodash", "4.17.21", window, known_at)
        self.assertEqual(first, second)
        self.assertIn("valid_at=2026-07-08T19:00:00Z", first)
        self.assertIn("valid_window=[2026-07-08T19:00:00Z,2026-07-08T20:30:00Z)", first)
        self.assertIn("known_at=2026-07-08T20:30:00Z", first)


if __name__ == "__main__":
    unittest.main()
