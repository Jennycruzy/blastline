"""Public GitHub lockfile history source."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlencode

from ..errors import ExternalCallError
from ..json_types import JsonValue, require_object, require_string
from ..model import Node, NodeType, repository_id
from ..store import GraphStore
from ..timeutil import parse_time
from .failures import FailureLedger
from .http import DiskHttpClient
from .lockfiles import graphify_lockfile, parse_lockfile
from .snapshots import LockfileSnapshot, SnapshotLedger


@dataclass(frozen=True, slots=True)
class GitHubCommit:
    sha: str
    committed_at: datetime


class GitHubLockfileSource:
    def __init__(
        self,
        http: DiskHttpClient,
        api_url: str,
        raw_url: str,
        store: GraphStore,
        ledger: FailureLedger,
        snapshots: SnapshotLedger,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.http = http
        self.api_url = api_url.rstrip("/")
        self.raw_url = raw_url.rstrip("/")
        self.store = store
        self.ledger = ledger
        self.snapshots = snapshots
        self.extra_headers = dict(extra_headers) if extra_headers is not None else None

    def commits(self, owner: str, repository: str, path: str, ref: str, limit: int) -> tuple[GitHubCommit, ...]:
        query = urlencode({"path": path, "sha": ref, "per_page": str(limit)})
        url = f"{self.api_url}/repos/{quote(owner)}/{quote(repository)}/commits?{query}"
        response = self.http.fetch(url, extra_headers=self.extra_headers)
        try:
            raw: JsonValue = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub commits response is invalid JSON") from exc
        if not isinstance(raw, list):
            raise ValueError("GitHub commits response must be an array")
        commits: list[GitHubCommit] = []
        for index, value in enumerate(raw):
            record = require_object(value, f"GitHub commit {index}")
            sha = require_string(record.get("sha"), f"GitHub commit {index}.sha")
            commit_value = require_object(record.get("commit"), f"GitHub commit {index}.commit")
            author_value = require_object(commit_value.get("author"), f"GitHub commit {index}.commit.author")
            date_value = require_string(author_value.get("date"), f"GitHub commit {index}.commit.author.date")
            commits.append(GitHubCommit(sha, parse_time(date_value, f"GitHub commit {index}.date")))
        commits.sort(key=lambda item: (item.committed_at, item.sha))
        return tuple(commits)

    def fetch_lockfile(self, owner: str, repository: str, path: str, sha: str, refresh: bool = False) -> bytes:
        url = self.raw_lockfile_url(owner, repository, path, sha)
        return self.http.fetch(url, refresh=refresh).body

    def raw_lockfile_url(self, owner: str, repository: str, path: str, sha: str) -> str:
        return f"{self.raw_url}/{quote(owner)}/{quote(repository)}/{quote(sha)}/{quote(path, safe='/')}"

    def ingest_history(
        self,
        owner: str,
        repository: str,
        path: str,
        ref: str,
        limit: int,
        ecosystem: str,
        commits_override: tuple[GitHubCommit, ...] | None = None,
        refresh: bool = False,
    ) -> tuple[int, int, int, str]:
        commits = commits_override if commits_override is not None else self.commits(owner, repository, path, ref, limit)
        if not commits:
            raise ValueError(f"GitHub returned no commits for {owner}/{repository}:{path}")
        repository_node_id = repository_id(repository_host(owner, repository), f"{owner}/{repository}")
        self.store.add_nodes(
            [
                Node(
                    repository_node_id,
                    NodeType.REPOSITORY,
                    {"host": repository_host(owner, repository), "full_name": f"{owner}/{repository}", "lockfile": path},
                )
            ]
        )
        parsed_snapshots = 0
        resolutions = 0
        failures = 0
        for index, commit in enumerate(commits):
            valid_to = commits[index + 1].committed_at if index + 1 < len(commits) else None
            identifier = f"{owner}/{repository}:{path}@{commit.sha}"
            try:
                raw_url = self.raw_lockfile_url(owner, repository, path, commit.sha)
                body = self.http.fetch(raw_url, refresh=refresh).body
                self.snapshots.record(
                    LockfileSnapshot.from_body(
                        f"{owner}/{repository}",
                        path,
                        commit.sha,
                        ecosystem,
                        commit.committed_at,
                        valid_to,
                        raw_url,
                        body,
                    )
                )
                result = parse_lockfile(path, body, ecosystem)
                for issue in result.issues:
                    self.ledger.record("github-lockfile", f"{identifier}:{issue.identifier}", issue.reason, body)
                    failures += 1
                nodes, edges = graphify_lockfile(repository_host(owner, repository), f"{owner}/{repository}", result, commit.committed_at, valid_to)
                self.store.add_nodes(nodes)
                self.store.add_edges(edges)
                parsed_snapshots += 1
                resolutions += len(result.resolutions)
            except (ExternalCallError, UnicodeDecodeError, TypeError, ValueError) as exc:
                self.ledger.record("github-lockfile", identifier, str(exc))
                failures += 1
        return parsed_snapshots, resolutions, failures, self.store.fingerprint()


def repository_host(owner: str, repository: str) -> str:
    del repository
    return "github.com"
