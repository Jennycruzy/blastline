"""Typed query outputs, including abstention and coverage."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..json_types import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class AbstentionNotice:
    scope: str
    reason: str

    def as_json(self) -> JsonObject:
        return {"scope": self.scope, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class Coverage:
    total_repositories: int
    resolvable_repositories: int
    unknown_repositories: int
    unknown_repository_ids: tuple[str, ...] = ()

    def as_json(self) -> JsonObject:
        return {
            "total_repositories": self.total_repositories,
            "resolvable_repositories": self.resolvable_repositories,
            "unknown_repositories": self.unknown_repositories,
            "unknown_repository_ids": list(self.unknown_repository_ids),
        }


@dataclass(frozen=True, slots=True)
class QueryResponse:
    query: str
    results: tuple[JsonObject, ...]
    abstentions: tuple[AbstentionNotice, ...]
    coverage: Coverage

    def as_json(self) -> JsonObject:
        return {
            "query": self.query,
            "results": list(self.results),
            "abstentions": [item.as_json() for item in self.abstentions],
            "coverage": self.coverage.as_json(),
        }

    def human(self) -> str:
        lines = [f"{self.query}: {len(self.results)} result(s)"]
        for result in self.results:
            lines.append(f"  {json.dumps(result, sort_keys=True)}")
        if self.abstentions:
            lines.append(f"  abstentions: {len(self.abstentions)}")
            for item in self.abstentions:
                lines.append(f"    {item.scope}: {item.reason}")
        lines.append(
            "  coverage: "
            f"{self.coverage.resolvable_repositories} of {self.coverage.total_repositories} repositories resolvable; "
            f"{self.coverage.unknown_repositories} unknown"
        )
        if self.coverage.unknown_repository_ids:
            lines.append("  unknown repositories: " + ", ".join(self.coverage.unknown_repository_ids))
        return "\n".join(lines)
