"""Measured agreement between Hydra-backed evidence and the local oracle."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..errors import Abstention
from ..json_types import JsonObject
from ..query.engine import QueryEngine
from ..query.hydra_evidence import HydraWindowVerifier
from ..store import GraphStore
from ..timeutil import format_time, now_utc
from .grader import VerificationCase, Verifier, git_commit_sha


@dataclass(frozen=True, slots=True)
class HydraAgreementCase:
    case: VerificationCase
    local_repositories: tuple[str, ...]
    hydra_candidate_paths: int
    hydra_verified_repositories: tuple[str, ...]
    agreement: bool
    hydra_abstentions: tuple[str, ...]
    rejected_source_count: int
    latency_ms: float | None
    false_confirmations: tuple[str, ...]
    false_omissions: tuple[str, ...]

    def as_json(self) -> JsonObject:
        return {
            "case": self.case.as_json(),
            "local_result": list(self.local_repositories),
            "hydra_candidate_paths": self.hydra_candidate_paths,
            "hydra_verified_result": list(self.hydra_verified_repositories),
            "agreement": self.agreement,
            "hydra_abstained": bool(self.hydra_abstentions),
            "hydra_abstentions": list(self.hydra_abstentions),
            "rejected_source_count": self.rejected_source_count,
            "latency_ms": self.latency_ms,
            "false_confirmations": list(self.false_confirmations),
            "false_omissions": list(self.false_omissions),
        }


@dataclass(frozen=True, slots=True)
class HydraAgreementScorecard:
    graph_fingerprint: str
    commit_sha: str
    cases: tuple[HydraAgreementCase, ...]
    executed_cases: int
    agreement_cases: int
    disagreement_cases: int
    hydra_abstentions: int
    false_confirmations: int
    false_omissions: int

    def as_json(self) -> JsonObject:
        return {
            "graph_fingerprint": self.graph_fingerprint,
            "commit_sha": self.commit_sha,
            "cases": [case.as_json() for case in self.cases],
            "executed_cases": self.executed_cases,
            "agreement_cases": self.agreement_cases,
            "disagreement_cases": self.disagreement_cases,
            "hydra_abstentions": self.hydra_abstentions,
            "false_confirmations": self.false_confirmations,
            "false_omissions": self.false_omissions,
        }

    def human(self) -> str:
        lines = ["HYDRA-BACKED AGREEMENT — disagreements first"]
        disagreements = [case for case in self.cases if not case.agreement]
        if disagreements:
            lines.append("disagreements:")
            for case in disagreements:
                lines.append(f"  {case.case.case_id}")
                lines.append(f"    local: {', '.join(case.local_repositories) or 'none'}")
                lines.append(f"    hydra: {', '.join(case.hydra_verified_repositories) or 'none'}")
                for reason in case.hydra_abstentions:
                    lines.append(f"    abstention: {reason}")
        else:
            lines.append("disagreements: none in this run")
        lines.extend(
            [
                f"cases executed: {self.executed_cases}",
                f"agreement: {self.agreement_cases}/{self.executed_cases}" if self.executed_cases else "agreement: not-defined",
                f"false confirmations: {self.false_confirmations}",
                f"false omissions: {self.false_omissions}",
                f"Hydra abstentions: {self.hydra_abstentions}",
                f"graph fingerprint: {self.graph_fingerprint}",
                f"commit SHA: {self.commit_sha}",
            ]
        )
        return "\n".join(lines)


class HydraAgreementVerifier:
    def __init__(self, store: GraphStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.local = Verifier(store, settings)
        self.engine = QueryEngine(store, settings)

    def grade(self) -> HydraAgreementScorecard:
        from ..hydra import HydraClient, load_hydra_config

        client = HydraClient(load_hydra_config(self.settings.root, self.settings.values))
        if not client.live_enabled:
            raise Abstention("HydraDB agreement scorecard requires HYDRA_DB_API_KEY")
        limit = self.settings.integer("hydra", "agreement_case_limit")
        cases = self.local.discover_cases()[:limit]
        if not cases:
            raise Abstention("no gradable verification cases available for Hydra agreement")
        runner = HydraWindowVerifier(client, self.store, self.engine, self.settings.integer("hydra", "candidate_result_limit"))
        results: list[HydraAgreementCase] = []
        for case in cases:
            local_response = self.engine.window_exposure(
                case.registry,
                case.package,
                case.version,
                (case.window_start, case.window_end),
                case.known_at,
            )
            local_repositories = tuple(
                sorted(
                    str(item["repository"])
                    for item in local_response.results
                    if isinstance(item.get("repository"), str)
                )
            )
            try:
                hydra_result = runner.run(
                    case.registry,
                    case.package,
                    case.version,
                    (case.window_start, case.window_end),
                    case.known_at,
                )
                hydra_repositories = tuple(
                    sorted(
                        str(item["repository"])
                        for item in hydra_result.accepted_results
                        if isinstance(item.get("repository"), str)
                    )
                )
                hydra_abstentions = hydra_result.abstentions
                latency = hydra_result.latency_ms
                candidate_paths = len(hydra_result.candidate_paths)
                rejected_count = len(hydra_result.rejected_source_ids)
            except Abstention as exc:
                hydra_repositories = ()
                hydra_abstentions = (str(exc),)
                latency = None
                candidate_paths = 0
                rejected_count = 0
            local_set = set(local_repositories)
            hydra_set = set(hydra_repositories)
            results.append(
                HydraAgreementCase(
                    case=case,
                    local_repositories=local_repositories,
                    hydra_candidate_paths=candidate_paths,
                    hydra_verified_repositories=hydra_repositories,
                    agreement=not hydra_abstentions and local_set == hydra_set,
                    hydra_abstentions=tuple(hydra_abstentions),
                    rejected_source_count=rejected_count,
                    latency_ms=latency,
                    false_confirmations=tuple(sorted(hydra_set - local_set)),
                    false_omissions=tuple(sorted(local_set - hydra_set)),
                )
            )
        return HydraAgreementScorecard(
            graph_fingerprint=self.store.fingerprint(),
            commit_sha=git_commit_sha(self.settings.root),
            cases=tuple(results),
            executed_cases=len(results),
            agreement_cases=sum(1 for case in results if case.agreement),
            disagreement_cases=sum(1 for case in results if not case.agreement),
            hydra_abstentions=sum(1 for case in results if case.hydra_abstentions),
            false_confirmations=sum(len(case.false_confirmations) for case in results),
            false_omissions=sum(len(case.false_omissions) for case in results),
        )

    def record(self, scorecard: HydraAgreementScorecard) -> Path:
        path = self.settings.root / "cache" / "verification" / "hydra-agreement.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = scorecard.as_json()
        payload["recorded_at"] = format_time(now_utc())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        return path
