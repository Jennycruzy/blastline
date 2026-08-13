"""Generate the judge-facing incident report from live graph queries."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .json_types import JsonObject
from .model import NodeType
from .query.engine import QueryEngine
from .store import GraphStore
from .timeutil import format_time, now_utc, parse_time
from .verify.grader import Verifier


def generate_incident_report(settings: Settings) -> tuple[Path, JsonObject]:
    """Run the configured incident queries and persist their real output."""

    store = GraphStore(settings.path("graph", "directory"))
    engine = QueryEngine(store, settings)
    timeline = settings.section("timeline")
    registry = settings.string("timeline", "demo_registry")
    package = settings.string("timeline", "demo_package")
    version = settings.string("timeline", "demo_version")
    start = parse_time(settings.string("timeline", "demo_from"), "timeline.demo_from")
    end = parse_time(settings.string("timeline", "demo_to"), "timeline.demo_to")

    historical = engine.window_exposure(registry, package, version, (start, end))
    present = engine.current_exposure(registry, package, version)
    dirty = engine.still_dirty(registry, package, version, (start, end))
    coverage = engine.coverage_report()

    verifier = Verifier(store, settings)
    scorecard = verifier.grade()
    verification_path = verifier.record(scorecard)

    historical_repositories = {
        item["repository"]
        for item in historical.results
        if isinstance(item.get("repository"), str)
    }
    present_repositories = {
        item["repository"]
        for item in present.results
        if isinstance(item.get("repository"), str)
    }
    node_counts: dict[str, int] = {}
    for node_type in NodeType:
        node_counts[node_type.value] = len(store.nodes_of_type(node_type))

    payload: JsonObject = {
        "generated_at": format_time(now_utc()),
        "graph": {
            "fingerprint": store.fingerprint(),
            "node_counts": node_counts,
            "edge_count": len(store.edges()),
        },
        "incident": {
            "registry": registry,
            "package": package,
            "version": version,
            "window": {"from": format_time(start), "to": format_time(end)},
        },
        "historical_exposure": historical.as_json(),
        "present_exposure": present.as_json(),
        "comparison": {
            "historical_repositories": sorted(historical_repositories),
            "present_repositories": sorted(present_repositories),
            "historical_only": sorted(historical_repositories - present_repositories),
            "present_only": sorted(present_repositories - historical_repositories),
        },
        "still_dirty": dirty.as_json(),
        "coverage": coverage.as_json(),
        "verification": scorecard.as_json(),
        "verification_record": str(verification_path.relative_to(settings.root)),
        "timeline_configuration": timeline,
    }

    artifact = settings.path("report", "artifact")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return artifact, payload
