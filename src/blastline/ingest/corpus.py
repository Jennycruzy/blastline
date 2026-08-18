"""Reproducible, real GitHub lockfile corpus discovery."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from ..config import Settings
from ..errors import ConfigurationError, ExternalCallError
from ..json_types import JsonObject, JsonValue, require_int, require_object, require_string
from ..model import EdgeType, NodeType
from ..store import GraphStore
from ..timeutil import format_time, now_utc, parse_time
from .failures import FailureLedger
from .github import GitHubCommit, GitHubLockfileSource
from .http import DiskHttpClient
from .snapshots import SnapshotLedger


LOCKFILE_ECOSYSTEMS = {
    "package-lock.json": "npm",
    "yarn.lock": "npm",
    "pnpm-lock.yaml": "npm",
    "poetry.lock": "pypi",
    "requirements.txt": "pypi",
    "requirements.in": "pypi",
}


@dataclass(frozen=True, slots=True)
class CorpusRepository:
    full_name: str
    path: str
    ref: str
    ecosystem: str
    matched_packages: tuple[str, ...]
    history_commits: tuple[GitHubCommit, ...]

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def identifier(self) -> str:
        return f"{self.full_name}:{self.path}"

    @property
    def history_shas(self) -> tuple[str, ...]:
        return tuple(commit.sha for commit in self.history_commits)

    def as_json(self) -> JsonObject:
        return {
            "full_name": self.full_name,
            "path": self.path,
            "ref": self.ref,
            "ecosystem": self.ecosystem,
            "matched_packages": list(self.matched_packages),
            "history_commits": [
                {"sha": commit.sha, "committed_at": format_time(commit.committed_at)}
                for commit in self.history_commits
            ],
        }

    @classmethod
    def from_json(cls, value: JsonObject, context: str, minimum_history_commits: int) -> "CorpusRepository":
        matched = _string_list(value.get("matched_packages"), f"{context}.matched_packages")
        history_value = value.get("history_commits")
        if not isinstance(history_value, list):
            raise ValueError(f"{context}.history_commits must be an array")
        history: list[GitHubCommit] = []
        for index, item in enumerate(history_value):
            record = require_object(item, f"{context}.history_commits[{index}]")
            history.append(
                GitHubCommit(
                    require_string(record.get("sha"), f"{context}.history_commits[{index}].sha"),
                    parse_time(
                        require_string(record.get("committed_at"), f"{context}.history_commits[{index}].committed_at"),
                        f"{context}.history_commits[{index}].committed_at",
                    ),
                )
            )
        if len({commit.sha for commit in history}) < minimum_history_commits:
            raise ValueError(f"{context}.history_shas has fewer than the configured minimum history commits")
        return cls(
            require_string(value.get("full_name"), f"{context}.full_name"),
            require_string(value.get("path"), f"{context}.path"),
            require_string(value.get("ref"), f"{context}.ref"),
            require_string(value.get("ecosystem"), f"{context}.ecosystem"),
            matched,
            tuple(history),
        )


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    source_graph_fingerprint: str
    generated_at: str
    implicated_packages: tuple[tuple[str, str], ...]
    selection_rule: JsonObject
    search_queries: tuple[JsonObject, ...]
    selected: tuple[CorpusRepository, ...]
    rejected: tuple[JsonObject, ...]

    def as_json(self) -> JsonObject:
        return {
            "schema_version": 1,
            "source_graph_fingerprint": self.source_graph_fingerprint,
            "generated_at": self.generated_at,
            "selection_rule": self.selection_rule,
            "implicated_packages": [
                {"registry": registry, "package": package}
                for registry, package in self.implicated_packages
            ],
            "search_queries": list(self.search_queries),
            "selected": [item.as_json() for item in self.selected],
            "rejected": list(self.rejected),
        }

    @classmethod
    def load(cls, path: Path) -> "CorpusManifest":
        try:
            value: JsonValue = json.loads(path.read_text(encoding="utf-8"))
            document = require_object(value, "GitHub corpus manifest")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"GitHub corpus manifest cannot be read: {path}") from exc
        version = require_int(document.get("schema_version"), "GitHub corpus manifest.schema_version")
        if version != 1:
            raise ValueError(f"unsupported GitHub corpus manifest schema: {version}")
        packages_value = document.get("implicated_packages")
        if not isinstance(packages_value, list):
            raise ValueError("GitHub corpus manifest.implicated_packages must be an array")
        packages: list[tuple[str, str]] = []
        for index, item in enumerate(packages_value):
            record = require_object(item, f"GitHub corpus manifest.implicated_packages[{index}]")
            packages.append(
                (
                    require_string(record.get("registry"), f"GitHub corpus manifest.implicated_packages[{index}].registry"),
                    require_string(record.get("package"), f"GitHub corpus manifest.implicated_packages[{index}].package"),
                )
            )
        selection_rule = require_object(document.get("selection_rule"), "GitHub corpus manifest.selection_rule")
        minimum_history_commits = require_int(
            selection_rule.get("minimum_historical_lockfile_commits"),
            "GitHub corpus manifest.selection_rule.minimum_historical_lockfile_commits",
        )
        if minimum_history_commits < 1:
            raise ValueError("GitHub corpus manifest minimum history commits must be positive")
        queries_value = document.get("search_queries")
        if not isinstance(queries_value, list):
            raise ValueError("GitHub corpus manifest.search_queries must be an array")
        queries = tuple(require_object(item, f"GitHub corpus manifest.search_queries[{index}]") for index, item in enumerate(queries_value))
        selected_value = document.get("selected")
        if not isinstance(selected_value, list):
            raise ValueError("GitHub corpus manifest.selected must be an array")
        selected = tuple(
            CorpusRepository.from_json(
                require_object(item, f"GitHub corpus manifest.selected[{index}]"),
                f"GitHub corpus manifest.selected[{index}]",
                minimum_history_commits,
            )
            for index, item in enumerate(selected_value)
        )
        rejected_value = document.get("rejected")
        if not isinstance(rejected_value, list):
            raise ValueError("GitHub corpus manifest.rejected must be an array")
        rejected = tuple(require_object(item, f"GitHub corpus manifest.rejected[{index}]") for index, item in enumerate(rejected_value))
        return cls(
            require_string(document.get("source_graph_fingerprint"), "GitHub corpus manifest.source_graph_fingerprint"),
            require_string(document.get("generated_at"), "GitHub corpus manifest.generated_at"),
            tuple(sorted(set(packages))),
            selection_rule,
            queries,
            selected,
            rejected,
        )


@dataclass(frozen=True, slots=True)
class GithubSearchHit:
    full_name: str
    path: str
    ref: str
    ecosystem: str


class GitHubCorpusDiscoverer:
    def __init__(self, store: GraphStore, http: DiskHttpClient, api_url: str, failures: FailureLedger) -> None:
        self.store = store
        self.http = http
        self.api_url = api_url.rstrip("/")
        self.failures = failures

    @staticmethod
    def authenticated_headers() -> dict[str, str]:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            raise ConfigurationError(
                "GitHub corpus discovery requires GITHUB_TOKEN or GH_TOKEN for authenticated code search"
            )
        return {"Authorization": f"Bearer {token}"}

    def implicated_packages(self) -> tuple[tuple[str, str], ...]:
        packages: set[tuple[str, str]] = set()
        for edge in self.store.edges():
            if edge.edge_type is not EdgeType.AFFECTS:
                continue
            version = self.store.node(edge.target_id)
            if version is None or version.node_type is not NodeType.VERSION:
                continue
            registry = version.attributes.get("registry")
            package = version.attributes.get("package")
            if not isinstance(registry, str) or not isinstance(package, str):
                raise ValueError(f"advisory edge targets version without registry/package identity: {edge.target_id}")
            packages.add((registry, package))
        return tuple(sorted(packages))

    def search_code(
        self,
        query: str,
        page: int,
        page_size: int,
        refresh: bool,
        headers: dict[str, str],
    ) -> tuple[tuple[GithubSearchHit, ...], int | None]:
        if page < 1 or page_size < 1:
            raise ValueError("GitHub code search page and page_size must be positive")
        parameters = urlencode({"q": query, "page": str(page), "per_page": str(page_size)})
        url = f"{self.api_url}/search/code?{parameters}"
        response = self.http.fetch(url, refresh=refresh, extra_headers=headers)
        try:
            value: JsonValue = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub code search response is invalid JSON") from exc
        document = require_object(value, "GitHub code search response")
        total_value = document.get("total_count")
        if total_value is not None and (isinstance(total_value, bool) or not isinstance(total_value, int)):
            raise ValueError("GitHub code search total_count must be an integer when present")
        items_value = document.get("items")
        if not isinstance(items_value, list):
            raise ValueError("GitHub code search items must be an array")
        hits: list[GithubSearchHit] = []
        for index, item_value in enumerate(items_value):
            item = require_object(item_value, f"GitHub code search item {index}")
            path = require_string(item.get("path"), f"GitHub code search item {index}.path")
            filename = path.rsplit("/", 1)[-1]
            ecosystem = LOCKFILE_ECOSYSTEMS.get(filename)
            if ecosystem is None:
                continue
            repository = require_object(item.get("repository"), f"GitHub code search item {index}.repository")
            private = repository.get("private")
            if private is True:
                continue
            full_name = require_string(repository.get("full_name"), f"GitHub code search item {index}.repository.full_name")
            ref = require_string(repository.get("default_branch"), f"GitHub code search item {index}.repository.default_branch")
            if full_name.count("/") != 1 or not all(full_name.split("/")):
                raise ValueError(f"GitHub code search item {index} has invalid repository name: {full_name}")
            hits.append(GithubSearchHit(full_name, path, ref, ecosystem))
        return tuple(hits), total_value if isinstance(total_value, int) else None

    def discover(
        self,
        settings: Settings,
        manifest_path: Path,
        repository_limit: int,
        candidate_limit_per_package: int,
        minimum_history_commits: int,
        maximum_per_owner: int,
        search_page_size: int,
        search_pages_per_query: int,
        history_limit: int,
        refresh: bool = False,
    ) -> CorpusManifest:
        if repository_limit < 1 or candidate_limit_per_package < 1 or minimum_history_commits < 1:
            raise ValueError("GitHub corpus limits must be positive")
        if maximum_per_owner < 1 or search_page_size < 1 or search_pages_per_query < 1 or history_limit < minimum_history_commits:
            raise ValueError("GitHub corpus selection limits are inconsistent")
        current_fingerprint = self.store.fingerprint()
        if manifest_path.exists() and not refresh:
            manifest = CorpusManifest.load(manifest_path)
            if manifest.source_graph_fingerprint != current_fingerprint:
                raise ValueError(
                    "cached GitHub corpus manifest was built from a different graph; "
                    "rerun discovery with --refresh"
                )
            return manifest

        headers = self.authenticated_headers()
        implicated = self.implicated_packages()
        if not implicated:
            raise ValueError("no OSV-implicated packages are available to seed GitHub corpus discovery")
        candidate_map: dict[tuple[str, str, str, str], set[str]] = {}
        query_records: list[JsonObject] = []
        for registry, package in implicated:
            package_hits = 0
            for filename, filename_ecosystem in sorted(LOCKFILE_ECOSYSTEMS.items()):
                if filename_ecosystem != registry:
                    continue
                query = f'"{package}" in:file filename:{filename}'
                for page in range(1, search_pages_per_query + 1):
                    hits, total_count = self.search_code(query, page, search_page_size, refresh, headers)
                    query_records.append(
                        {
                            "registry": registry,
                            "package": package,
                            "filename": filename,
                            "query": query,
                            "page": page,
                            "returned": len(hits),
                            "total_count": total_count,
                        }
                    )
                    for hit in hits:
                        key = (hit.full_name, hit.path, hit.ref, hit.ecosystem)
                        candidate_map.setdefault(key, set()).add(package)
                        package_hits += 1
                        if package_hits >= candidate_limit_per_package:
                            break
                    if package_hits >= candidate_limit_per_package or not hits:
                        break

        candidates = sorted(candidate_map.items(), key=lambda item: item[0])
        selected: list[CorpusRepository] = []
        rejected: list[JsonObject] = []
        owner_counts: dict[str, int] = {}
        source = GitHubLockfileSource(
            self.http,
            self.api_url,
            settings.string("ingest", "github_raw_url"),
            self.store,
            self.failures,
            # Discovery only uses the authenticated commits endpoint. Raw lockfiles
            # remain separately cached and public during the ingestion pass.
            settings_snapshot_ledger(settings),
            headers,
        )
        for (full_name, path, ref, ecosystem), packages in candidates:
            if len(selected) >= repository_limit:
                rejected.append({"repository": full_name, "path": path, "reason": "repository limit reached"})
                continue
            owner = full_name.split("/", 1)[0]
            if owner_counts.get(owner, 0) >= maximum_per_owner:
                rejected.append({"repository": full_name, "path": path, "reason": "owner cap reached"})
                continue
            identifier = f"{full_name}:{path}"
            try:
                commits = source.commits(*full_name.split("/", 1), path, ref, history_limit)
            except (ExternalCallError, TypeError, ValueError) as exc:
                self.failures.record("github-corpus-discovery", identifier, str(exc))
                rejected.append({"repository": full_name, "path": path, "reason": f"history lookup failed: {exc}"})
                continue
            unique_commits = tuple({commit.sha: commit for commit in commits}.values())
            if len(unique_commits) < minimum_history_commits:
                rejected.append(
                    {
                        "repository": full_name,
                        "path": path,
                        "reason": "fewer than the configured minimum historical lockfile commits",
                        "history_commits": len(unique_commits),
                    }
                )
                continue
            selected.append(CorpusRepository(full_name, path, ref, ecosystem, tuple(sorted(packages)), unique_commits))
            owner_counts[owner] = owner_counts.get(owner, 0) + 1

        manifest = CorpusManifest(
            current_fingerprint,
            format_time(now_utc()),
            implicated,
            {
                "seed": "packages attached to real OSV AFFECTS edges in the current graph",
                "search": "GitHub code search for the package name inside a supported lockfile",
                "minimum_historical_lockfile_commits": minimum_history_commits,
                "include_repositories_without_the_affected_version": True,
                "organization_cap": maximum_per_owner,
                "repository_limit": repository_limit,
                "candidate_limit_per_package": candidate_limit_per_package,
                "search_page_size": search_page_size,
                "search_pages_per_query": search_pages_per_query,
                "history_limit": history_limit,
            },
            tuple(query_records),
            tuple(selected),
            tuple(rejected),
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return manifest


def settings_snapshot_ledger(settings: Settings) -> SnapshotLedger:
    """Resolve the configured ledger without importing the pipeline."""

    return SnapshotLedger(settings.path("verification", "lockfile_snapshot_ledger"))


def _string_list(value: JsonValue | None, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{context} must be an array of strings")
    return tuple(value)
