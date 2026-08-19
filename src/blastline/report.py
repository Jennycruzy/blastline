"""Generate the incident report from live graph queries."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .errors import Abstention
from .hydra import HydraClient, load_hydra_config
from .json_types import JsonObject
from .model import NodeType
from .query.engine import QueryEngine
from .query.hydra_evidence import HydraWindowVerifier
from .store import GraphStore
from .timeutil import format_time, now_utc, parse_time
from .verify.grader import Verifier
from .verify.manual_holdout import ManualHoldoutVerifier


def _latest_jsonl(path: Path) -> JsonObject | None:
    if not path.exists():
        return None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    value = json.loads(lines[-1])
    return value if isinstance(value, dict) else None


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
    current_risk = engine.still_dirty(registry, package, version, (start, end))
    coverage = engine.coverage_report()

    verifier = Verifier(store, settings)
    scorecard = verifier.grade()
    verification_path = verifier.record(scorecard)
    manual_holdout = ManualHoldoutVerifier(settings).grade()

    hydra_client = HydraClient(load_hydra_config(settings.root, settings.values))
    hydra_window: JsonObject
    hydra_agreement: JsonObject
    if hydra_client.live_enabled:
        hydra_window_path = settings.root / "cache" / "verification" / "hydra-window.jsonl"
        latest_window = _latest_jsonl(hydra_window_path)
        expected_target = f"{registry}:{package}@{version}"
        if (
            latest_window is not None
            and latest_window.get("graph_fingerprint") == store.fingerprint()
            and latest_window.get("target") == expected_target
        ):
            hydra_window = latest_window
            hydra_window["record"] = str(hydra_window_path.relative_to(settings.root))
        else:
            hydra_window = {
                "status": "ABSTAINED",
                "reason": "no recorded Hydra window run matches the current target and graph fingerprint",
            }
        agreement_path = settings.root / "cache" / "verification" / "hydra-agreement.jsonl"
        latest_agreement = _latest_jsonl(agreement_path)
        if latest_agreement is not None and latest_agreement.get("graph_fingerprint") == store.fingerprint():
            hydra_agreement = latest_agreement
            hydra_agreement["record"] = str(agreement_path.relative_to(settings.root))
        else:
            hydra_agreement = {
                "status": "ABSTAINED",
                "reason": "no recorded Hydra agreement scorecard matches the current graph fingerprint",
            }
    else:
        hydra_window = {"status": "ABSTAINED", "reason": "HYDRA_DB_API_KEY is not set"}
        hydra_agreement = {"status": "ABSTAINED", "reason": "HYDRA_DB_API_KEY is not set"}

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
        "still_dirty": current_risk.as_json(),
        "coverage": coverage.as_json(),
        "verification": scorecard.as_json(),
        "verification_record": str(verification_path.relative_to(settings.root)),
        "manual_parser_holdout": manual_holdout.as_json(),
        "hydra_window": hydra_window,
        "hydra_agreement": hydra_agreement,
        "timeline_configuration": timeline,
    }

    artifact = settings.path("report", "artifact")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return artifact, payload
