"""Measured graph and ingestion coverage artifact."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .json_types import JsonObject, require_object, require_string
from .model import NodeType
from .store import GraphStore


def _read_object(path: Path, description: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} cannot be read: {path}") from exc
    return require_object(value, description)


def _failure_counts(path: Path) -> tuple[dict[str, int], int]:
    by_source: dict[str, int] = {}
    total = 0
    if not path.exists():
        return by_source, total
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"failure ledger cannot be read: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = require_object(json.loads(line), f"failure ledger line {line_number}")
            source = require_string(record.get("source"), f"failure ledger line {line_number}.source")
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"failure ledger line {line_number} is invalid") from exc
        by_source[source] = by_source.get(source, 0) + 1
        total += 1
    return by_source, total


def _observed_counts(store: GraphStore, registry: str) -> dict[str, int]:
    return {
        "packages": sum(1 for node in store.nodes_of_type(NodeType.PACKAGE) if node.attributes.get("registry") == registry),
        "versions": sum(1 for node in store.nodes_of_type(NodeType.VERSION) if node.attributes.get("registry") == registry),
        "maintainers": sum(1 for node in store.nodes_of_type(NodeType.MAINTAINER) if node.attributes.get("registry") == registry),
    }


def generate_coverage_report(settings: Settings) -> tuple[Path, JsonObject]:
    store = GraphStore(settings.path("graph", "directory"))
    denominator_path = settings.root / "cache" / "coverage" / "registry-denominators.json"
    denominators = _read_object(denominator_path, "registry denominator file") if denominator_path.exists() else {}
    failures, failure_total = _failure_counts(settings.root / "cache" / "ingest-failures.jsonl")
    registries: dict[str, JsonObject] = {}
    for registry in ("npm", "pypi"):
        observed = _observed_counts(store, registry)
        denominator_record = denominators.get(f"{registry}:package_names")
        denominator: int | None = None
        denominator_source: str | None = None
        if denominator_record is not None:
            record = require_object(denominator_record, f"denominator {registry}")
            value = record.get("denominator")
            if isinstance(value, bool) or (value is not None and not isinstance(value, int)):
                raise ValueError(f"denominator {registry} must be an integer")
            denominator = value
            denominator_source = require_string(record.get("source"), f"denominator {registry}.source")
        coverage_percent: float | None = None
        if denominator is not None and denominator > 0:
            coverage_percent = round(observed["packages"] * 100.0 / denominator, 6)
        registries[registry] = {
            **observed,
            "package_name_denominator": denominator,
            "package_name_denominator_source": denominator_source,
            "package_name_coverage_percent": coverage_percent,
            "coverage_status": "measured" if denominator is not None else "not-measured",
        }
    payload: JsonObject = {
        "graph_fingerprint": store.fingerprint(),
        "node_counts": {node_type.value: len(store.nodes_of_type(node_type)) for node_type in NodeType},
        "edge_count": len(store.edges()),
        "registries": registries,
        "repositories": {
            "total": len(store.nodes_of_type(NodeType.REPOSITORY)),
        },
        "failures": {
            "total": failure_total,
            "by_source": failures,
        },
    }
    artifact = settings.root / "examples" / "coverage-report.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return artifact, payload
