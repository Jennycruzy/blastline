"""Verify lockfile parsing against manually reviewed raw-payload labels."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..config import Settings
from ..ingest.http import DiskHttpClient, HttpPolicy
from ..ingest.lockfiles import parse_lockfile
from ..ingest.snapshots import SnapshotLedger
from ..json_types import JsonObject, JsonValue, require_bool, require_object, require_string


@dataclass(frozen=True, slots=True)
class ManualHoldoutResult:
    snapshot_id: str
    package: str
    version: str
    expected_present: bool
    actual_present: bool
    case_class: str
    raw_evidence: str

    def as_json(self) -> JsonObject:
        return {
            "snapshot_id": self.snapshot_id,
            "package": self.package,
            "version": self.version,
            "expected_present": self.expected_present,
            "actual_present": self.actual_present,
            "case_class": self.case_class,
            "raw_evidence": self.raw_evidence,
            "passed": self.expected_present == self.actual_present,
        }


@dataclass(frozen=True, slots=True)
class ManualHoldoutScorecard:
    results: tuple[ManualHoldoutResult, ...]
    labeling_method: str
    reviewed_at: str

    @property
    def passed(self) -> int:
        return sum(item.expected_present == item.actual_present for item in self.results)

    @property
    def positive_cases(self) -> int:
        return sum(item.expected_present for item in self.results)

    @property
    def negative_cases(self) -> int:
        return len(self.results) - self.positive_cases

    @property
    def case_classes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.results:
            counts[item.case_class] = counts.get(item.case_class, 0) + 1
        return dict(sorted(counts.items()))

    def as_json(self) -> JsonObject:
        return {
            "cases": len(self.results),
            "positive_cases": self.positive_cases,
            "negative_cases": self.negative_cases,
            "passed": self.passed,
            "failed": len(self.results) - self.passed,
            "case_classes": self.case_classes,
            "labeling_method": self.labeling_method,
            "reviewed_at": self.reviewed_at,
            "results": [item.as_json() for item in self.results],
        }

    def human(self) -> str:
        lines = [
            "MANUALLY REVIEWED LOCKFILE HOLDOUT",
            (
                f"cases: {len(self.results)}; positives: {self.positive_cases}; "
                f"negatives: {self.negative_cases}; passed: {self.passed}; "
                f"failed: {len(self.results) - self.passed}"
            ),
        ]
        lines.extend(
            f"  {'PASS' if item.expected_present == item.actual_present else 'FAIL'} "
            f"{item.snapshot_id}: {item.package}@{item.version} [{item.case_class}]"
            for item in self.results
        )
        return "\n".join(lines)


class ManualHoldoutVerifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        policy = HttpPolicy(
            settings.integer("hydra", "request_timeout_seconds"),
            1,
            settings.number("hydra", "retry_base_seconds"),
        )
        self.http = DiskHttpClient(
            settings.path("hydra", "cache_directory").parent / "registry",
            policy,
            settings.string("ingest", "user_agent"),
        )

    def grade(self) -> ManualHoldoutScorecard:
        document = self._document()
        cases_value = document.get("cases")
        if not isinstance(cases_value, list) or not cases_value:
            raise ValueError("manual holdout cases must be a non-empty array")
        snapshots = {
            item.snapshot_id: item
            for item in SnapshotLedger(self.settings.path("verification", "lockfile_snapshot_ledger")).load()
        }
        results: list[ManualHoldoutResult] = []
        for index, value in enumerate(cases_value):
            case = require_object(value, f"manual holdout cases[{index}]")
            snapshot_id = require_string(case.get("snapshot_id"), f"manual holdout cases[{index}].snapshot_id")
            raw_url = require_string(case.get("raw_url"), f"manual holdout cases[{index}].raw_url")
            payload_hash = require_string(case.get("payload_hash"), f"manual holdout cases[{index}].payload_hash")
            ecosystem = require_string(case.get("ecosystem"), f"manual holdout cases[{index}].ecosystem")
            package = require_string(case.get("package"), f"manual holdout cases[{index}].package")
            version = require_string(case.get("version"), f"manual holdout cases[{index}].version")
            expected = require_bool(case.get("expected_present"), f"manual holdout cases[{index}].expected_present")
            case_class = require_string(case.get("case_class"), f"manual holdout cases[{index}].case_class")
            evidence = require_string(case.get("raw_evidence"), f"manual holdout cases[{index}].raw_evidence")
            snapshot = snapshots.get(snapshot_id)
            if snapshot is None or snapshot.raw_url != raw_url or snapshot.payload_hash != payload_hash:
                raise ValueError(f"manual holdout provenance does not match the snapshot ledger: {snapshot_id}")
            body = self.http.read_cached(raw_url).body
            actual_hash = hashlib.sha256(body).hexdigest()
            if actual_hash != payload_hash:
                raise ValueError(f"manual holdout payload hash mismatch: {snapshot_id}")
            parsed = parse_lockfile(snapshot.path, body, ecosystem)
            actual = any(
                item.ecosystem == ecosystem and item.package_name == package and item.version == version
                for item in parsed.resolutions
            )
            results.append(ManualHoldoutResult(snapshot_id, package, version, expected, actual, case_class, evidence))
        scorecard = ManualHoldoutScorecard(
            tuple(results),
            require_string(document.get("labeling_method"), "manual holdout labeling_method"),
            require_string(document.get("reviewed_at"), "manual holdout reviewed_at"),
        )
        if scorecard.passed != len(scorecard.results):
            raise ValueError("manual lockfile holdout contains parser disagreements")
        return scorecard

    def _document(self) -> JsonObject:
        path = self.settings.path("verification", "manual_holdout")
        try:
            value: JsonValue = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read manual holdout: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"manual holdout is invalid JSON: {path}") from exc
        return require_object(value, "manual holdout")
