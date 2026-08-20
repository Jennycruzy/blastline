"""Compare graph predictions with real parsed lockfile resolutions."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..config import Settings
from ..json_types import JsonObject
from ..ingest.http import DiskHttpClient, HttpPolicy
from ..ingest.snapshots import SnapshotLedger
from ..model import Edge, EdgeType, NodeType
from ..query.engine import QueryEngine
from ..query.types import QueryResponse
from ..store import GraphStore
from ..timeutil import format_time, now_utc
from .lockfile_oracle import CachedLockfileOracle


@dataclass(frozen=True, slots=True)
class VerificationCase:
    case_id: str
    registry: str
    package: str
    version: str
    window_start: datetime
    window_end: datetime
    known_at: datetime
    observed_repositories: tuple[str, ...]
    observation_abstentions: tuple[str, ...]

    def as_json(self) -> JsonObject:
        return {
            "case_id": self.case_id,
            "registry": self.registry,
            "package": self.package,
            "version": self.version,
            "window_start": format_time(self.window_start),
            "window_end": format_time(self.window_end),
            "known_at": format_time(self.known_at),
            "observed_repositories": list(self.observed_repositories),
            "observation_abstentions": list(self.observation_abstentions),
            "selection_source": "raw-snapshot-ledger",
        }


@dataclass(frozen=True, slots=True)
class VerificationCaseResult:
    case: VerificationCase
    predicted_repositories: tuple[str, ...]
    observed_repositories: tuple[str, ...]
    false_negatives: tuple[str, ...]
    false_positives: tuple[str, ...]
    abstentions: tuple[str, ...]

    def as_json(self) -> JsonObject:
        return {
            "case": self.case.as_json(),
            "predicted_repositories": list(self.predicted_repositories),
            "observed_repositories": list(self.observed_repositories),
            "false_negatives": list(self.false_negatives),
            "false_positives": list(self.false_positives),
            "abstentions": list(self.abstentions),
            "gradable": not self.abstentions,
        }


@dataclass(frozen=True, slots=True)
class Scorecard:
    graph_fingerprint: str
    commit_sha: str
    cases: tuple[VerificationCaseResult, ...]
    gradable_cases: int
    ungradable_cases: int
    true_positives: int
    false_negatives: int
    false_positives: int
    precision: float | None
    recall: float | None

    def as_json(self) -> JsonObject:
        positive_pair_decisions = self.true_positives + self.false_positives + self.false_negatives
        return {
            "graph_fingerprint": self.graph_fingerprint,
            "commit_sha": self.commit_sha,
            "cases": [case.as_json() for case in self.cases],
            "gradable_cases": self.gradable_cases,
            "ungradable_cases": self.ungradable_cases,
            "true_positives": self.true_positives,
            "false_negatives": self.false_negatives,
            "false_positives": self.false_positives,
            "positive_pair_decisions": positive_pair_decisions,
            "observed_positive_pairs": self.true_positives + self.false_negatives,
            "predicted_positive_pairs": self.true_positives + self.false_positives,
            "precision": self.precision,
            "recall": self.recall,
        }

    def human(self) -> str:
        lines = ["VERIFICATION — false negatives first"]
        misses = [case for case in self.cases if case.false_negatives]
        if misses:
            lines.append("false negatives:")
            for case in misses:
                lines.append(f"  {case.case.case_id}: {', '.join(case.false_negatives)}")
        else:
            lines.append("false negatives: none in this run")
        lines.extend(
            [
                f"cases: {self.gradable_cases} gradable, {self.ungradable_cases} ungradable and excluded",
                (
                    "positive repository-pair decisions: "
                    f"{self.true_positives + self.false_positives + self.false_negatives} "
                    f"across {self.gradable_cases} temporal cases (true negatives not enumerated)"
                ),
                f"confusion counts: TP={self.true_positives} FP={self.false_positives} FN={self.false_negatives}",
                f"precision: {format_metric(self.precision)}",
                f"recall: {format_metric(self.recall)}",
                f"graph fingerprint: {self.graph_fingerprint}",
                f"commit SHA: {self.commit_sha}",
            ]
        )
        return "\n".join(lines)


class Verifier:
    def __init__(self, store: GraphStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.engine = QueryEngine(store, settings)
        cache_path = settings.path("hydra", "cache_directory").parent / "registry"
        policy = HttpPolicy(
            settings.integer("hydra", "request_timeout_seconds"),
            settings.integer("hydra", "retry_attempts"),
            settings.number("hydra", "retry_base_seconds"),
        )
        user_agent = settings.string("ingest", "user_agent")
        self.oracle = CachedLockfileOracle(
            SnapshotLedger(settings.path("verification", "lockfile_snapshot_ledger")),
            DiskHttpClient(cache_path, policy, user_agent),
        )

    def discover_cases(self) -> tuple[VerificationCase, ...]:
        cases: list[VerificationCase] = []
        default_days = self.settings.integer("graph", "default_window_days")
        limit = self.settings.integer("verification", "repository_limit")
        affected: dict[tuple[str, str, str], Edge] = {}
        for advisory_edge in sorted(self.store.edges(), key=lambda item: item.edge_id):
            if advisory_edge.edge_type is not EdgeType.AFFECTS:
                continue
            version_node = self.store.node(advisory_edge.target_id)
            if version_node is None or version_node.node_type is not NodeType.VERSION:
                continue
            registry = version_node.attributes.get("registry")
            package = version_node.attributes.get("package")
            version = version_node.attributes.get("version")
            if not isinstance(registry, str) or not isinstance(package, str) or not isinstance(version, str):
                continue
            key = (registry, package, version)
            previous = affected.get(key)
            if previous is None or (advisory_edge.commit_at, advisory_edge.edge_id) < (previous.commit_at, previous.edge_id):
                affected[key] = advisory_edge

        seen: set[tuple[str, str, str, datetime, datetime, datetime]] = set()
        for target in self.oracle.discover_targets(set(affected)):
            advisory_edge = affected[(target.registry, target.package, target.version)]
            window_start = max(advisory_edge.valid.start, target.snapshot.committed_at)
            window_end = min(
                target.snapshot.valid_to or window_start + timedelta(days=default_days),
                window_start + timedelta(days=default_days),
            )
            if advisory_edge.valid.end is not None:
                window_end = min(window_end, advisory_edge.valid.end)
            if window_end <= window_start:
                continue
            known_at = max(advisory_edge.commit_at, target.snapshot.committed_at)
            key = (target.registry, target.package, target.version, window_start, window_end, known_at)
            if key in seen:
                continue
            seen.add(key)
            observation = self.oracle.observe(
                target.registry,
                target.package,
                target.version,
                (window_start, window_end),
                known_at,
            )
            case_id = (
                f"{target.package}@{target.version}:{format_time(window_start)}:"
                f"snapshot:{target.snapshot.snapshot_id}"
            )
            cases.append(
                VerificationCase(
                    case_id,
                    target.registry,
                    target.package,
                    target.version,
                    window_start,
                    window_end,
                    known_at,
                    observation.repositories,
                    observation.abstentions,
                )
            )
            if len(cases) >= limit:
                return tuple(cases)
        return tuple(cases)

    def grade(self) -> Scorecard:
        cases = self.discover_cases()
        if not cases:
            raise ValueError("no gradable OSV-backed parsed-lockfile verification cases found")
        results: list[VerificationCaseResult] = []
        true_positives = 0
        false_negatives = 0
        false_positives = 0
        gradable = 0
        for case in cases:
            # Verification cases describe an exposure interval.  Q1 at the
            # interval start can miss a repository that first resolves the
            # target later in that same interval; Q3 is the temporal query
            # whose interval-intersection semantics match the oracle.
            response = self.engine.window_exposure(
                case.registry,
                case.package,
                case.version,
                (case.window_start, case.window_end),
                known_at=case.known_at,
            )
            predicted = {
                str(item["repository"])
                for item in response.results
                if isinstance(item.get("repository"), str)
            }
            observed = set(case.observed_repositories)
            misses = tuple(sorted(observed - predicted))
            extras = tuple(sorted(predicted - observed))
            abstentions = tuple(case.observation_abstentions) + tuple(
                f"{item.scope}: {item.reason}" for item in response.abstentions
            )
            result = VerificationCaseResult(case, tuple(sorted(predicted)), tuple(sorted(observed)), misses, extras, abstentions)
            results.append(result)
            if abstentions:
                continue
            gradable += 1
            true_positives += len(predicted & observed)
            false_negatives += len(observed - predicted)
            false_positives += len(predicted - observed)
        precision_denominator = true_positives + false_positives
        recall_denominator = true_positives + false_negatives
        return Scorecard(
            graph_fingerprint=self.store.fingerprint(),
            commit_sha=git_commit_sha(self.settings.root),
            cases=tuple(results),
            gradable_cases=gradable,
            ungradable_cases=len(cases) - gradable,
            true_positives=true_positives,
            false_negatives=false_negatives,
            false_positives=false_positives,
            precision=true_positives / precision_denominator if precision_denominator else None,
            recall=true_positives / recall_denominator if recall_denominator else None,
        )

    def record(self, scorecard: Scorecard) -> Path:
        path = self.settings.root / "cache" / "verification" / "runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = scorecard.as_json()
        payload["recorded_at"] = format_time(now_utc())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        return path

    def repository_label(self, node_id: str) -> str:
        node = self.store.node(node_id)
        if node is None:
            return node_id
        full_name = node.attributes.get("full_name")
        return full_name if isinstance(full_name, str) else node_id

    @staticmethod
    def _interval(start: datetime, end: datetime):
        from ..model import TimeInterval

        return TimeInterval(start, end)


def git_commit_sha(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot attribute verification to a git commit") from exc
    sha = completed.stdout.strip()
    if not sha:
        raise RuntimeError("git returned an empty commit SHA")
    return sha


def format_metric(value: float | None) -> str:
    return "not-defined" if value is None else f"{value:.4f}"
