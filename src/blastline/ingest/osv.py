"""OSV.dev advisory ingestion and conservative version matching."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from ..errors import ExternalCallError
from ..json_types import JsonObject, JsonValue, require_object, require_string
from ..model import Edge, EdgeType, Node, NodeType, TimeInterval, version_id
from ..semver import UnsupportedRange, satisfies
from ..store import GraphStore
from ..timeutil import parse_time
from .failures import FailureLedger
from .http import DiskHttpClient, HttpPolicy


@dataclass(frozen=True, slots=True)
class OsvTarget:
    ecosystem: str
    package_name: str
    version: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class OsvVulnerability:
    advisory_id: str
    summary: str
    published_at: datetime
    modified_at: datetime
    affected_ranges: tuple[str, ...]
    affected_versions: tuple[str, ...]


class OsvClient:
    def __init__(self, http: DiskHttpClient, endpoint: str, vulnerability_endpoint: str) -> None:
        self.http = http
        self.endpoint = endpoint
        self.vulnerability_endpoint = vulnerability_endpoint.rstrip("/")

    def query(self, targets: tuple[OsvTarget, ...]) -> tuple[tuple[OsvVulnerability, ...], ...]:
        queries: list[JsonValue] = []
        for target in targets:
            queries.append(
                {
                    "package": {"ecosystem": target.ecosystem, "name": target.package_name},
                    "version": target.version,
                }
            )
        payload: JsonObject = {"queries": queries}
        response = self.http.fetch(
            self.endpoint,
            method="POST",
            body=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        )
        document = require_object(json.loads(response.body), "OSV query response")
        results_value = document.get("results")
        if not isinstance(results_value, list) or len(results_value) != len(targets):
            raise ValueError("OSV response results do not match query count")
        parsed: list[tuple[OsvVulnerability, ...]] = []
        for index, result_value in enumerate(results_value):
            result = require_object(result_value, f"OSV result {index}")
            vulns_value = result.get("vulns")
            if vulns_value is None:
                parsed.append(())
                continue
            if not isinstance(vulns_value, list):
                raise ValueError(f"OSV result {index}.vulns must be an array when present")
            vulns: list[OsvVulnerability] = []
            for vuln_index, vuln_value in enumerate(vulns_value):
                vuln_context = f"OSV result {index}.vulns[{vuln_index}]"
                vuln_object = require_object(vuln_value, vuln_context)
                if not isinstance(vuln_object.get("published"), str) or not isinstance(vuln_object.get("affected"), list):
                    advisory_id = require_string(vuln_object.get("id"), f"{vuln_context}.id")
                    detail_response = self.http.fetch(f"{self.vulnerability_endpoint}/{quote(advisory_id, safe='')}")
                    try:
                        detail_value: JsonValue = json.loads(detail_response.body)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"OSV advisory {advisory_id} detail is invalid JSON") from exc
                    vuln_value = detail_value
                vulns.append(parse_vulnerability(vuln_value, vuln_context))
            parsed.append(tuple(vulns))
        return tuple(parsed)


def parse_vulnerability(value: JsonValue, context: str) -> OsvVulnerability:
    document = require_object(value, context)
    advisory_id = require_string(document.get("id"), f"{context}.id")
    published = require_string(document.get("published"), f"{context}.published")
    modified_value = document.get("modified")
    modified = require_string(modified_value, f"{context}.modified") if modified_value is not None else published
    summary_value = document.get("summary")
    summary = summary_value if isinstance(summary_value, str) else "OSV advisory"
    affected_value = document.get("affected")
    if not isinstance(affected_value, list):
        raise ValueError(f"{context}.affected must be an array")
    ranges: list[str] = []
    versions: list[str] = []
    for affected_index, affected_item in enumerate(affected_value):
        affected = require_object(affected_item, f"{context}.affected[{affected_index}]")
        versions_value = affected.get("versions")
        if versions_value is not None:
            if not isinstance(versions_value, list) or not all(isinstance(item, str) for item in versions_value):
                raise ValueError(f"{context}.affected[{affected_index}].versions must be strings")
            versions.extend(str(item) for item in versions_value)
        ranges_value = affected.get("ranges")
        if not isinstance(ranges_value, list):
            continue
        for range_index, range_item in enumerate(ranges_value):
            range_object = require_object(range_item, f"{context}.ranges[{range_index}]")
            events_value = range_object.get("events")
            if not isinstance(events_value, list):
                raise ValueError(f"{context}.ranges[{range_index}].events must be an array")
            events: list[str] = []
            for event in events_value:
                event_object = require_object(event, f"{context}.ranges[{range_index}].event")
                introduced = event_object.get("introduced")
                fixed = event_object.get("fixed")
                if isinstance(introduced, str):
                    events.append(f">={introduced}")
                if isinstance(fixed, str):
                    events.append(f"<{fixed}")
            if events:
                ranges.append(" ".join(events))
    return OsvVulnerability(
        advisory_id=advisory_id,
        summary=summary,
        published_at=parse_time(published, f"{context}.published"),
        modified_at=parse_time(modified, f"{context}.modified"),
        affected_ranges=tuple(ranges),
        affected_versions=tuple(versions),
    )


def vulnerability_matches(vulnerability: OsvVulnerability, target: OsvTarget, ecosystem: str) -> bool:
    if target.version in vulnerability.affected_versions:
        return True
    for expression in vulnerability.affected_ranges:
        try:
            if satisfies(target.version, expression, ecosystem):
                return True
        except UnsupportedRange:
            continue
    return False


def attach_advisories(
    store: GraphStore,
    client: OsvClient,
    targets: tuple[OsvTarget, ...],
    batch_size: int,
    ledger: FailureLedger,
) -> tuple[int, int]:
    advisory_nodes: list[Node] = []
    advisory_edges: list[Edge] = []
    matched = 0
    failures = 0
    for start in range(0, len(targets), batch_size):
        batch = targets[start : start + batch_size]
        try:
            results = client.query(batch)
        except (ExternalCallError, TypeError, ValueError) as exc:
            ledger.record("osv", f"batch:{start}", str(exc))
            failures += len(batch)
            continue
        for target, vulnerabilities in zip(batch, results):
            for vulnerability in vulnerabilities:
                if not vulnerability_matches(vulnerability, target, target.ecosystem):
                    continue
                matched += 1
                advisory_id = f"advisory:osv:{vulnerability.advisory_id}"
                version_node_id = version_id(
                    "npm" if target.ecosystem.lower() == "npm" else "pypi",
                    target.package_name,
                    target.version,
                )
                advisory_nodes.append(
                    Node(
                        advisory_id,
                        NodeType.ADVISORY,
                        {
                            "id": vulnerability.advisory_id,
                            "ecosystem": target.ecosystem,
                            "summary": vulnerability.summary,
                            "published_at": vulnerability.published_at.isoformat(),
                            "modified_at": vulnerability.modified_at.isoformat(),
                        },
                    )
                )
                advisory_edges.append(
                    Edge.create(
                        advisory_id,
                        EdgeType.AFFECTS,
                        version_node_id,
                        TimeInterval(target.published_at),
                        vulnerability.published_at,
                        {
                            "source": "OSV.dev affected version/range",
                            "validity_precision": "installable-version lower bound; malicious introduction date unavailable",
                        },
                    )
                )
    store.add_nodes(advisory_nodes)
    store.add_edges(advisory_edges)
    return matched, failures
