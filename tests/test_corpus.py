from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from blastline.config import Settings
from blastline.ingest.corpus import GitHubCorpusDiscoverer
from blastline.ingest.failures import FailureLedger
from blastline.ingest.http import HttpResponse
from blastline.model import Edge, EdgeType, Node, NodeType, TimeInterval, version_id
from blastline.store import GraphStore


ROOT = Path(__file__).resolve().parents[1]


class FakeGitHubHttp:
    def fetch(self, url: str, **kwargs: object) -> HttpResponse:
        del kwargs
        parsed = urlparse(url)
        if parsed.path.endswith("/search/code"):
            query = parse_qs(parsed.query)["q"][0]
            if "filename:package-lock.json" in query:
                body = {
                    "total_count": 1,
                    "items": [
                        {
                            "path": "package-lock.json",
                            "repository": {
                                "full_name": "example/service",
                                "default_branch": "main",
                                "private": False,
                            },
                        }
                    ],
                }
            else:
                body = {"total_count": 0, "items": []}
            return HttpResponse(url, 200, {}, json.dumps(body).encode("utf-8"), False)
        if "/commits" in parsed.path:
            body = [
                {"sha": f"commit-{index}", "commit": {"author": {"date": f"2026-08-{13 + index:02d}T00:00:00Z"}}}
                for index in range(3)
            ]
            return HttpResponse(url, 200, {}, json.dumps(body).encode("utf-8"), False)
        raise AssertionError(f"unexpected GitHub URL: {url}")


class CorpusDiscoveryTest(unittest.TestCase):
    def test_selection_is_real_search_backed_and_manifest_replays_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = copy.deepcopy(Settings.load(ROOT).values)
            values["graph"]["directory"] = str(root / "graph")
            values["verification"]["lockfile_snapshot_ledger"] = str(root / "snapshots.jsonl")
            settings = Settings(ROOT, values)
            store = GraphStore(root / "graph")
            start = datetime(2026, 8, 13, tzinfo=timezone.utc)
            version = version_id("npm", "lodash", "4.17.21")
            advisory = "advisory:osv:TEST-1"
            store.add_nodes(
                [
                    Node(version, NodeType.VERSION, {"registry": "npm", "package": "lodash", "version": "4.17.21"}),
                    Node(advisory, NodeType.ADVISORY, {"id": "TEST-1"}),
                ]
            )
            store.add_edges([Edge.create(advisory, EdgeType.AFFECTS, version, TimeInterval(start), start)])

            manifest_path = root / "corpus.json"
            failures = FailureLedger(root / "failures.jsonl")
            discoverer = GitHubCorpusDiscoverer(store, FakeGitHubHttp(), "https://api.github.com", failures)  # type: ignore[arg-type]
            old_token = os.environ.get("GITHUB_TOKEN")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                manifest = discoverer.discover(
                    settings,
                    manifest_path,
                    repository_limit=1,
                    candidate_limit_per_package=1,
                    minimum_history_commits=3,
                    maximum_per_owner=1,
                    search_page_size=10,
                    search_pages_per_query=1,
                    history_limit=3,
                )
            finally:
                if old_token is None:
                    os.environ.pop("GITHUB_TOKEN", None)
                else:
                    os.environ["GITHUB_TOKEN"] = old_token

            self.assertEqual(len(manifest.selected), 1)
            self.assertEqual(manifest.selected[0].full_name, "example/service")
            self.assertEqual(manifest.selected[0].history_shas, ("commit-0", "commit-1", "commit-2"))
            self.assertEqual(manifest.implicated_packages, (("npm", "lodash"),))
            self.assertEqual(manifest.selection_rule["minimum_historical_lockfile_commits"], 3)

            replayed = GitHubCorpusDiscoverer(store, FakeGitHubHttp(), "https://api.github.com", failures).discover(
                settings,
                manifest_path,
                repository_limit=1,
                candidate_limit_per_package=1,
                minimum_history_commits=3,
                maximum_per_owner=1,
                search_page_size=10,
                search_pages_per_query=1,
                history_limit=3,
            )
            self.assertEqual(replayed, manifest)


if __name__ == "__main__":
    unittest.main()
