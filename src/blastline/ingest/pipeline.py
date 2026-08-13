"""Resumable registry ingestion with measured, itemized accounting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..errors import BlastlineError, ExternalCallError
from ..json_types import JsonObject
from ..model import Edge, Node
from ..store import GraphStore
from .failures import FailureLedger
from .graphify import graphify_package
from .http import DiskHttpClient, HttpPolicy
from .parsers import parse_npm, parse_pypi
from .simple_index import parse_simple_index
from .sources import NpmRegistry, PyPIRegistry, read_checkpoint, write_checkpoint


@dataclass(slots=True)
class IngestReport:
    source: str
    packages_seen: int = 0
    versions_seen: int = 0
    nodes_added: int = 0
    edges_added: int = 0
    failures: int = 0
    cached_requests: int = 0

    def as_json(self) -> JsonObject:
        return {
            "source": self.source,
            "packages_seen": self.packages_seen,
            "versions_seen": self.versions_seen,
            "nodes_added": self.nodes_added,
            "edges_added": self.edges_added,
            "failures": self.failures,
            "cached_requests": self.cached_requests,
        }


class RegistryIngestor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        cache_path = settings.path("hydra", "cache_directory").parent / "registry"
        ingest_section = settings.section("ingest")
        user_agent_value = ingest_section.get("user_agent")
        if not isinstance(user_agent_value, str):
            raise BlastlineError("configuration ingest.user_agent must be a string")
        timeout = settings.integer("hydra", "request_timeout_seconds")
        attempts = settings.integer("hydra", "retry_attempts")
        retry_base = settings.number("hydra", "retry_base_seconds")
        self.http = DiskHttpClient(cache_path, HttpPolicy(timeout, attempts, retry_base), user_agent_value)
        self.npm = NpmRegistry(
            self.http,
            self._string(ingest_section, "npm_registry_url"),
            self._string(ingest_section, "npm_changes_url"),
        )
        self.pypi = PyPIRegistry(
            self.http,
            self._string(ingest_section, "pypi_package_url"),
            self._string(ingest_section, "pypi_simple_url"),
        )
        self.store = GraphStore(settings.path("graph", "directory"))
        self.ledger = FailureLedger(settings.root / "cache" / "ingest-failures.jsonl")

    @staticmethod
    def _string(section: JsonObject, key: str) -> str:
        value = section.get(key)
        if not isinstance(value, str):
            raise BlastlineError(f"configuration ingest.{key} must be a string")
        return value

    def _add_package(self, package_body: bytes, source: str, report: IngestReport) -> None:
        try:
            if source == "npm":
                package, issues = parse_npm(package_body, source)
            elif source == "pypi":
                package, issues = parse_pypi(package_body, source)
            else:
                raise ValueError(f"unsupported registry source: {source}")
        except (TypeError, ValueError) as exc:
            self.ledger.record(source, source, str(exc), package_body)
            report.failures += 1
            return
        report.packages_seen += 1
        report.versions_seen += len(package.versions)
        for issue in issues:
            self.ledger.record(issue.source, issue.identifier, issue.reason, package_body)
            report.failures += 1
        try:
            nodes, edges = graphify_package(package)
            report.nodes_added += self.store.add_nodes(nodes)
            report.edges_added += self.store.add_edges(edges)
        except (TypeError, ValueError) as exc:
            self.ledger.record(source, package.source_identifier, f"graphification failed: {exc}", package_body)
            report.failures += 1

    def npm_packages(self, names: tuple[str, ...], refresh: bool = False) -> IngestReport:
        report = IngestReport("npm")
        for name in names:
            try:
                response = self.npm.package(name, refresh=refresh)
            except ExternalCallError as exc:
                self.ledger.record("npm", name, str(exc))
                report.failures += 1
                continue
            if response.from_cache:
                report.cached_requests += 1
            self._add_package(response.body, "npm", report)
        return report

    def pypi_packages(self, names: tuple[str, ...], refresh: bool = False) -> IngestReport:
        report = IngestReport("pypi")
        for name in names:
            try:
                response = self.pypi.package(name, refresh=refresh)
            except ExternalCallError as exc:
                self.ledger.record("pypi", name, str(exc))
                report.failures += 1
                continue
            if response.from_cache:
                report.cached_requests += 1
            self._add_package(response.body, "pypi", report)
        return report

    def npm_changes(self, limit: int, refresh: bool = False) -> IngestReport:
        report = IngestReport("npm-changes")
        checkpoint = self.settings.root / "cache" / "checkpoints" / "npm.json"
        since = read_checkpoint(checkpoint, "npm", self._string(self.settings.section("ingest"), "npm_initial_since"))
        changes = self.npm.changes(since, limit, refresh=refresh)
        for change in changes.results:
            deleted = change.get("deleted")
            document = change.get("doc")
            identifier = str(change.get("id", "unknown"))
            if deleted is True:
                self.ledger.record("npm", identifier, "registry reported deletion")
                report.failures += 1
                continue
            if not isinstance(document, dict):
                self.ledger.record("npm", identifier, "change has no embedded package document")
                report.failures += 1
                continue
            self._add_package(json.dumps(document, sort_keys=True).encode(), "npm", report)
        write_checkpoint(checkpoint, "npm", changes.last_seq)
        if changes.results and report.cached_requests == 0:
            report.cached_requests = 0
        return report

    def pypi_simple(self, limit: int, refresh: bool = False) -> IngestReport:
        response = self.pypi.simple_index(refresh=refresh)
        names = parse_simple_index(response.body)
        selected = names[:limit]
        return self.pypi_packages(selected, refresh=refresh)

    def print_report(self, report: IngestReport) -> None:
        print(
            f"{report.source}: ingested {report.versions_seen} versions across "
            f"{report.packages_seen} packages, added {report.nodes_added} nodes and "
            f"{report.edges_added} edges; failed {report.failures}; "
            f"cached requests {report.cached_requests}; graph fingerprint {self.store.fingerprint()}"
        )
        print(f"unparsed or failed records in this run: {report.failures}")
