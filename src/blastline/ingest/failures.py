"""Append-only accounting for records that cannot be safely ingested."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..json_types import JsonObject
from ..timeutil import format_time, now_utc


@dataclass(frozen=True, slots=True)
class Failure:
    source: str
    identifier: str
    reason: str
    payload_hash: str | None

    def as_json(self) -> JsonObject:
        result: JsonObject = {
            "source": self.source,
            "identifier": self.identifier,
            "reason": self.reason,
        }
        if self.payload_hash is not None:
            result["payload_hash"] = self.payload_hash
        return result


class FailureLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.failures: list[Failure] = []

    def record(self, source: str, identifier: str, reason: str, payload: bytes | None = None) -> None:
        payload_hash = hashlib.sha256(payload).hexdigest() if payload is not None else None
        failure = Failure(source, identifier, reason, payload_hash)
        self.failures.append(failure)
        record: JsonObject = failure.as_json()
        record["recorded_at"] = format_time(now_utc())
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            handle.write("\n")

    @property
    def count(self) -> int:
        return len(self.failures)
