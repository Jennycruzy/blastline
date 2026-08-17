"""Resumable registry ingestion with measured, itemized accounting."""

from __future__ import annotations

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..errors import BlastlineError, ExternalCallError
from ..json_types import JsonObject
from ..model import Edge, Node, NodeType, repository_id
from ..hydra import HydraClient, load_hydra_config, response_success
from ..store import GraphStore
from .failures import FailureLedger
from .github import GitHubLockfileSource
from .graphify import graphify_package
from .http import DiskHttpClient, HttpPolicy, HttpResponse
from .lockfiles import graphify_lockfile, parse_lockfile
from .osv import OsvClient, OsvTarget, attach_advisories
from .parsers import parse_npm, parse_pypi
from .simple_index import parse_simple_index
from .sources import (
    NpmRegistry,
    PyPIRegistry,
    read_checkpoint,
    read_cursor_checkpoint,
    read_index_checkpoint,
    write_checkpoint,
    write_cursor_checkpoint,
    write_index_checkpoint,
)


@dataclass(slots=True)
class IngestReport:
    source: str
    packages_seen: int = 0
    versions_seen: int = 0
    nodes_added: int = 0
    edges_added: int = 0
    failures: int = 0
    cached_requests: int = 0
    feed_results: int = 0
    feed_exhausted: bool = True
    catalog_total: int | None = None
    catalog_selected: int | None = None
    catalog_completed: int | None = None

    def as_json(self) -> JsonObject:
        return {
            "source": self.source,
            "packages_seen": self.packages_seen,
            "versions_seen": self.versions_seen,
            "nodes_added": self.nodes_added,
            "edges_added": self.edges_added,
            "failures": self.failures,
            "cached_requests": self.cached_requests,
            "feed_results": self.feed_results,
            "feed_exhausted": self.feed_exhausted,
            "catalog_total": self.catalog_total,
            "catalog_selected": self.catalog_selected,
            "catalog_completed": self.catalog_completed,
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
        self.hydra = HydraClient(load_hydra_config(settings.root, settings.values))
        self.coverage_path = settings.root / "cache" / "coverage" / "registry-denominators.json"

    def _record_registry_denominator(self, registry: str, entity: str, denominator: int, source: str) -> None:
        if denominator < 0:
            raise BlastlineError("registry denominator cannot be negative")
        existing: JsonObject = {}
        if self.coverage_path.exists():
            try:
                parsed = json.loads(self.coverage_path.read_text(encoding="utf-8"))
                if not isinstance(parsed, dict):
                    raise ValueError("coverage denominator file must be an object")
                existing = parsed
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ExternalCallError(f"registry denominator file cannot be read: {self.coverage_path}") from exc
        existing[f"{registry}:{entity}"] = {
            "registry": registry,
            "entity": entity,
            "denominator": denominator,
            "source": source,
        }
        self.coverage_path.parent.mkdir(parents=True, exist_ok=True)
        self.coverage_path.write_text(json.dumps(existing, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def measure_registry_denominators(self, refresh: bool = False) -> None:
        """Record denominators from authoritative catalog responses only."""

        npm_page = self.npm.all_docs(None, None, 1, refresh=refresh)
        if npm_page.total_rows is None:
            raise ExternalCallError("npm catalog did not publish total_rows; denominator is not measurable")
        self._record_registry_denominator(
            "npm",
            "package_names",
            npm_page.total_rows,
            "npm replication _all_docs.total_rows",
        )
        pypi_response = self.pypi.simple_index(refresh=refresh)
        pypi_names = parse_simple_index(pypi_response.body)
        self._record_registry_denominator(
            "pypi",
            "package_names",
            len(pypi_names),
            "PyPI Simple index entries",
        )

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
            self._publish_graph_records(nodes, edges)
        except (TypeError, ValueError) as exc:
            self.ledger.record(source, package.source_identifier, f"graphification failed: {exc}", package_body)
            report.failures += 1

    def _publish_graph_records(self, nodes: list[Node], edges: list[Edge]) -> None:
        if not self.hydra.live_enabled:
            return
        batch_size = self.settings.integer("hydra", "batch_size")
        if batch_size < 1:
            raise BlastlineError("configuration hydra.batch_size must be positive")
        graph_fingerprint = self.store.fingerprint()
        records: list[tuple[str, str, JsonObject]] = []
        for node in nodes:
            records.append(
                (
                    f"blastline:{node.node_id}",
                    json.dumps(node.as_json(), sort_keys=True),
                    {
                        "blastline_record_type": "node",
                        "blastline_node_type": node.node_type.value,
                        "blastline_node_id": node.node_id,
                        "graph_fingerprint": graph_fingerprint,
                    },
                )
            )
        for edge in edges:
            records.append(
                (
                    f"blastline:{edge.edge_id}",
                    json.dumps(edge.as_json(), sort_keys=True),
                    {
                        "blastline_record_type": "edge",
                        "blastline_edge_type": edge.edge_type.value,
                        "edge_id": edge.edge_id,
                        "source_id": edge.source_id,
                        "target_id": edge.target_id,
                        "valid_start": edge.valid.as_json()["start"],
                        "valid_end": edge.valid.as_json().get("end"),
                        "commit_at": edge.commit_at.isoformat().replace("+00:00", "Z"),
                        "graph_fingerprint": graph_fingerprint,
                    },
                )
            )
        for start in range(0, len(records), batch_size):
            response = self.hydra.add_memories(tuple(records[start : start + batch_size]))
            if not response_success(response):
                raise ExternalCallError("HydraDB rejected a Blastline graph batch")
            success_count = response.body.get("success_count")
            failed_count = response.body.get("failed_count")
            if success_count != len(records[start : start + batch_size]) or failed_count != 0:
                raise ExternalCallError("HydraDB graph batch did not report complete success")

    def npm_packages(self, names: tuple[str, ...], refresh: bool = False) -> IngestReport:
        report = IngestReport("npm")
        for batch in self._batches(names):
            with ThreadPoolExecutor(max_workers=self._fetch_concurrency()) as executor:
                fetched = list(executor.map(lambda name: self._fetch_npm(name, refresh), batch))
            for name, response, error in fetched:
                if error is not None:
                    self.ledger.record("npm", name, error)
                    report.failures += 1
                    continue
                if response is None:
                    raise RuntimeError("npm fetch returned neither response nor error")
                if response.from_cache:
                    report.cached_requests += 1
                self._add_package(response.body, "npm", report)
        return report

    def pypi_packages(self, names: tuple[str, ...], refresh: bool = False) -> IngestReport:
        report = IngestReport("pypi")
        for batch in self._batches(names):
            with ThreadPoolExecutor(max_workers=self._fetch_concurrency()) as executor:
                fetched = list(executor.map(lambda name: self._fetch_pypi(name, refresh), batch))
            for name, response, error in fetched:
                if error is not None:
                    self.ledger.record("pypi", name, error)
                    report.failures += 1
                    continue
                if response is None:
                    raise RuntimeError("PyPI fetch returned neither response nor error")
                if response.from_cache:
                    report.cached_requests += 1
                self._add_package(response.body, "pypi", report)
        return report

    def _fetch_concurrency(self) -> int:
        value = self.settings.integer("ingest", "fetch_concurrency")
        if value < 1:
            raise BlastlineError("configuration ingest.fetch_concurrency must be positive")
        return value

    def _batches(self, names: tuple[str, ...]):
        batch_size = self.settings.integer("hydra", "batch_size")
        if batch_size < 1:
            raise BlastlineError("configuration hydra.batch_size must be positive")
        for start in range(0, len(names), batch_size):
            yield names[start : start + batch_size]

    def _fetch_npm(self, name: str, refresh: bool) -> tuple[str, HttpResponse | None, str | None]:
        try:
            return name, self.npm.package(name, refresh=refresh), None
        except ExternalCallError as exc:
            return name, None, str(exc)

    def _fetch_pypi(self, name: str, refresh: bool) -> tuple[str, HttpResponse | None, str | None]:
        try:
            return name, self.pypi.package(name, refresh=refresh), None
        except ExternalCallError as exc:
            return name, None, str(exc)

    def npm_changes(self, limit: int, refresh: bool = False) -> IngestReport:
        if limit < 1:
            raise BlastlineError("npm changes page limit must be positive")
        report = IngestReport("npm-changes")
        checkpoint = self.settings.root / "cache" / "checkpoints" / "npm.json"
        since = read_checkpoint(checkpoint, "npm", self._string(self.settings.section("ingest"), "npm_initial_since"))
        changes = self.npm.changes(since, limit, refresh=refresh)
        if changes.results and changes.last_seq == since:
            raise ExternalCallError("npm changes feed did not advance its checkpoint")
        report.feed_results = len(changes.results)
        report.feed_exhausted = len(changes.results) < limit
        fetched_changes: list[tuple[JsonObject, HttpResponse | None, str | None]] = []
        with ThreadPoolExecutor(max_workers=self._fetch_concurrency()) as executor:
            futures = [executor.submit(self._fetch_change_package, change, refresh) for change in changes.results]
            for future in futures:
                fetched_changes.append(future.result())
        transient_fetch_failures = False
        for change, package_response, fetch_error in fetched_changes:
            deleted = change.get("deleted")
            identifier = str(change.get("id", "unknown"))
            if deleted is True:
                self.ledger.record("npm", identifier, "registry reported deletion")
                report.failures += 1
                continue
            if fetch_error is not None:
                if "failed with 404" in fetch_error:
                    self.ledger.record("npm", identifier, "registry reported deletion")
                else:
                    self.ledger.record("npm", identifier, fetch_error)
                    transient_fetch_failures = True
                report.failures += 1
                continue
            if package_response is None:
                raise RuntimeError("npm change fetch returned neither response nor error")
            if package_response.from_cache:
                report.cached_requests += 1
            self._add_package(package_response.body, "npm", report)
        if transient_fetch_failures:
            self.print_report(report)
            raise ExternalCallError("npm changes checkpoint not advanced because package metadata fetches failed")
        write_checkpoint(checkpoint, "npm", changes.last_seq)
        return report

    def npm_catalog(self, limit: int, refresh: bool = False) -> None:
        """Bootstrap npm from the supported paginated catalog endpoint.

        The current npm replication API does not return packuments inline in
        `_changes` and rejects a historical `since=0` bootstrap in practice.
        `_all_docs` is the supported bulk catalog path; package documents are
        fetched from the authoritative registry and each page is checkpointed
        only after its non-deletion records have been handled.
        """

        if limit < 1:
            raise BlastlineError("npm catalog page limit must be positive")
        checkpoint = self.settings.root / "cache" / "checkpoints" / "npm-catalog.json"
        startkey, startkey_docid, complete = read_cursor_checkpoint(checkpoint, "npm-catalog")
        if complete:
            print("npm-catalog: checkpoint is complete; no pages to ingest")
            return
        while True:
            page = self.npm.all_docs(startkey, startkey_docid, limit, refresh=refresh)
            if page.total_rows is not None:
                self._record_registry_denominator("npm", "package_names", page.total_rows, "npm replication _all_docs.total_rows")
            rows = list(page.results)
            if startkey_docid is not None and rows and rows[0].get("id") == startkey_docid:
                rows = rows[1:]
            if not rows:
                write_cursor_checkpoint(checkpoint, "npm-catalog", startkey, startkey_docid, True)
                print("npm-catalog: catalog exhausted; checkpoint complete")
                return
            report = IngestReport("npm-catalog", feed_results=len(rows), feed_exhausted=page.exhausted)
            fetched: list[tuple[JsonObject, HttpResponse | None, str | None]] = []
            with ThreadPoolExecutor(max_workers=self._fetch_concurrency()) as executor:
                futures = [executor.submit(self._fetch_change_package, row, refresh) for row in rows]
                for future in futures:
                    fetched.append(future.result())
            transient_fetch_failures = False
            for row, package_response, fetch_error in fetched:
                identifier = str(row.get("id", "unknown"))
                if fetch_error is not None:
                    if "failed with 404" in fetch_error:
                        self.ledger.record("npm-catalog", identifier, "registry reported deletion")
                    else:
                        self.ledger.record("npm-catalog", identifier, fetch_error)
                        transient_fetch_failures = True
                    report.failures += 1
                    continue
                if package_response is None:
                    raise RuntimeError("npm catalog fetch returned neither response nor error")
                if package_response.from_cache:
                    report.cached_requests += 1
                self._add_package(package_response.body, "npm", report)
            if transient_fetch_failures:
                self.print_report(report)
                raise ExternalCallError("npm catalog checkpoint not advanced because package metadata fetches failed")
            write_cursor_checkpoint(checkpoint, "npm-catalog", page.next_key, page.next_docid, page.exhausted)
            self.print_report(report)
            if page.exhausted:
                return
            startkey, startkey_docid = page.next_key, page.next_docid

    def _fetch_change_package(
        self,
        change: JsonObject,
        refresh: bool,
    ) -> tuple[JsonObject, HttpResponse | None, str | None]:
        identifier = change.get("id")
        if not isinstance(identifier, str):
            raise ValueError("npm change has no string id")
        document = change.get("doc")
        if isinstance(document, dict):
            return change, HttpResponse(identifier, 200, {}, json.dumps(document, sort_keys=True).encode(), True), None
        try:
            return change, self.npm.package(identifier, refresh=refresh), None
        except ExternalCallError as exc:
            return change, None, str(exc)

    def pypi_simple(self, limit: int | None, refresh: bool = False) -> IngestReport:
        response = self.pypi.simple_index(refresh=refresh)
        names = parse_simple_index(response.body)
        self._record_registry_denominator("pypi", "package_names", len(names), "PyPI Simple index entries")
        selected = names if limit is None else names[:limit]
        if limit is not None and limit < 1:
            raise BlastlineError("PyPI simple index limit must be positive or omitted for the full index")
        selected_fingerprint = hashlib.sha256("\n".join(selected).encode("utf-8")).hexdigest()
        checkpoint = self.settings.root / "cache" / "checkpoints" / "pypi.json"
        next_index = read_index_checkpoint(checkpoint, "pypi", selected_fingerprint, len(selected))
        report = IngestReport(
            "pypi-simple",
            catalog_total=len(names),
            catalog_selected=len(selected),
            catalog_completed=next_index,
        )
        batch_size = self.settings.integer("hydra", "batch_size")
        if batch_size < 1:
            raise BlastlineError("configuration hydra.batch_size must be positive")
        for start in range(next_index, len(selected), batch_size):
            batch_report = self.pypi_packages(selected[start : start + batch_size], refresh=refresh)
            self._merge_reports(report, batch_report)
            report.catalog_completed = start + len(selected[start : start + batch_size])
            write_index_checkpoint(checkpoint, "pypi", selected_fingerprint, report.catalog_completed)
        return report

    @staticmethod
    def _merge_reports(target: IngestReport, source: IngestReport) -> None:
        target.packages_seen += source.packages_seen
        target.versions_seen += source.versions_seen
        target.nodes_added += source.nodes_added
        target.edges_added += source.edges_added
        target.failures += source.failures
        target.cached_requests += source.cached_requests
        target.feed_results += source.feed_results
        target.feed_exhausted = target.feed_exhausted and source.feed_exhausted

    def github_lockfile(
        self,
        repository: str,
        path: str,
        ref: str,
        ecosystem: str,
    ) -> tuple[int, int, int, str]:
        parts = repository.split("/", 1)
        if len(parts) != 2 or not all(parts):
            raise BlastlineError("GitHub repository must be owner/name")
        ingest_section = self.settings.section("ingest")
        source = GitHubLockfileSource(
            self.http,
            self._string(ingest_section, "github_api_url"),
            self._string(ingest_section, "github_raw_url"),
            self.store,
            self.ledger,
        )
        limit = self.settings.integer("ingest", "github_history_limit")
        return source.ingest_history(parts[0], parts[1], path, ref, limit, ecosystem)

    def local_lockfile(
        self,
        path: Path,
        repository: str,
        ecosystem: str,
        valid_from: str,
        valid_to: str | None,
    ) -> tuple[int, int, int, str]:
        """Check a user-provided lockfile and retain an unknown repository on failure."""

        from ..timeutil import parse_time

        try:
            body = path.read_bytes()
        except OSError as exc:
            raise BlastlineError(f"cannot read local lockfile {path}: {exc}") from exc
        repository_node_id = repository_id("local", repository)
        self.store.add_nodes([Node(repository_node_id, NodeType.REPOSITORY, {"host": "local", "full_name": repository, "lockfile": str(path)})])
        try:
            result = parse_lockfile(path.name, body, ecosystem)
            start = parse_time(valid_from, "local lockfile valid_from")
            end = parse_time(valid_to, "local lockfile valid_to") if valid_to is not None else None
            nodes, edges = graphify_lockfile("local", repository, result, start, end)
            self.store.add_nodes(nodes)
            self.store.add_edges(edges)
            for issue in result.issues:
                self.ledger.record("local-lockfile", f"{path}:{issue.identifier}", issue.reason, body)
            return len(result.resolutions), len(result.issues), 0, self.store.fingerprint()
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            self.ledger.record("local-lockfile", str(path), f"unparseable lockfile: {exc}", body)
            return 0, 0, 1, self.store.fingerprint()

    def osv_package(
        self,
        registry: str,
        package_name: str,
        versions: tuple[str, ...] | None = None,
    ) -> tuple[int, int, str]:
        ecosystem = "npm" if registry.lower() == "npm" else "PyPI"
        selected_versions = set(versions) if versions is not None else None
        targets: list[OsvTarget] = []
        for node in self.store.nodes_of_type(NodeType.VERSION):
            if node.attributes.get("registry") != registry or node.attributes.get("package") != package_name:
                continue
            version_value = node.attributes.get("version")
            published_value = node.attributes.get("published_at")
            if not isinstance(version_value, str) or not isinstance(published_value, str):
                continue
            if selected_versions is not None and version_value not in selected_versions:
                continue
            from ..timeutil import parse_time

            try:
                published_at = parse_time(published_value, f"{node.node_id}.published_at")
            except ValueError as exc:
                self.ledger.record("osv", node.node_id, str(exc))
                continue
            targets.append(OsvTarget(ecosystem, package_name, version_value, published_at))
        if not targets:
            raise BlastlineError(f"no graph versions available for OSV lookup: {registry}:{package_name}")
        osv_section = self.settings.section("osv")
        endpoint = self._string(osv_section, "endpoint")
        vulnerability_endpoint = self._string(osv_section, "vulnerability_endpoint")
        batch_size = osv_section.get("batch_size")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise BlastlineError("configuration osv.batch_size must be an integer")
        client = OsvClient(self.http, endpoint, vulnerability_endpoint)
        matched, failures = attach_advisories(self.store, client, tuple(targets), batch_size, self.ledger)
        return matched, failures, self.store.fingerprint()

    def print_report(self, report: IngestReport) -> None:
        feed_detail = ""
        if report.source in ("npm-changes", "npm-catalog"):
            feed_detail = f"; feed/catalog records {report.feed_results}; exhausted {report.feed_exhausted}"
        if report.catalog_total is not None:
            feed_detail += (
                f"; catalog coverage {report.catalog_completed or 0}/{report.catalog_selected or 0} selected"
                f" of {report.catalog_total} published names"
            )
        print(
            f"{report.source}: ingested {report.versions_seen} versions across "
            f"{report.packages_seen} packages, added {report.nodes_added} nodes and "
            f"{report.edges_added} edges; failed {report.failures}; "
            f"cached requests {report.cached_requests}{feed_detail}; graph fingerprint {self.store.fingerprint()}"
        )
        print(f"unparsed or failed records in this run: {report.failures}")
