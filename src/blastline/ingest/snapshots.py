"""Append-only provenance for raw lockfile snapshots used by verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..json_types import JsonObject, require_object, require_string
from ..timeutil import format_time, parse_time


@dataclass(frozen=True, slots=True)
class LockfileSnapshot:
    repository: str
    path: str
    sha: str
    ecosystem: str
    committed_at: datetime
    valid_to: datetime | None
    raw_url: str
    payload_hash: str

    def __post_init__(self) -> None:
        if self.valid_to is not None and self.valid_to <= self.committed_at:
            raise ValueError("time interval end must be after start")

    @property
    def snapshot_id(self) -> str:
        return f"{self.repository}:{self.path}@{self.sha}"

    @classmethod
    def from_json(cls, value: JsonObject, context: str) -> "LockfileSnapshot":
        valid_to_value = value.get("valid_to")
        if valid_to_value is not None and not isinstance(valid_to_value, str):
            raise ValueError(f"{context}.valid_to must be a timestamp or null")
        return cls(
            repository=require_string(value.get("repository"), f"{context}.repository"),
            path=require_string(value.get("path"), f"{context}.path"),
            sha=require_string(value.get("sha"), f"{context}.sha"),
            ecosystem=require_string(value.get("ecosystem"), f"{context}.ecosystem"),
            committed_at=parse_time(require_string(value.get("committed_at"), f"{context}.committed_at"), f"{context}.committed_at"),
            valid_to=parse_time(valid_to_value, f"{context}.valid_to") if isinstance(valid_to_value, str) else None,
            raw_url=require_string(value.get("raw_url"), f"{context}.raw_url"),
            payload_hash=require_string(value.get("payload_hash"), f"{context}.payload_hash"),
        )

    @classmethod
    def from_body(
        cls,
        repository: str,
        path: str,
        sha: str,
        ecosystem: str,
        committed_at: datetime,
        valid_to: datetime | None,
        raw_url: str,
        body: bytes,
    ) -> "LockfileSnapshot":
        return cls(
            repository=repository,
            path=path,
            sha=sha,
            ecosystem=ecosystem,
            committed_at=committed_at,
            valid_to=valid_to,
            raw_url=raw_url,
            payload_hash=hashlib.sha256(body).hexdigest(),
        )

    def as_json(self) -> JsonObject:
        result: JsonObject = {
            "repository": self.repository,
            "path": self.path,
            "sha": self.sha,
            "ecosystem": self.ecosystem,
            "committed_at": format_time(self.committed_at),
            "raw_url": self.raw_url,
            "payload_hash": self.payload_hash,
            "valid_to": format_time(self.valid_to) if self.valid_to is not None else None,
        }
        return result


class SnapshotLedger:
    """Idempotent append-only ledger of fetched raw lockfile snapshots."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._existing: dict[str, LockfileSnapshot] | None = None

    def load(self) -> tuple[LockfileSnapshot, ...]:
        if not self.path.exists():
            return ()
        snapshots: list[LockfileSnapshot] = []
        seen: set[str] = set()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"cannot read snapshot ledger: {self.path}") from exc
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"snapshot ledger line {line_number} is invalid JSON") from exc
            snapshot = LockfileSnapshot.from_json(
                require_object(value, f"snapshot ledger line {line_number}"),
                f"snapshot ledger line {line_number}",
            )
            if snapshot.snapshot_id in seen:
                raise ValueError(f"snapshot ledger contains duplicate snapshot: {snapshot.snapshot_id}")
            seen.add(snapshot.snapshot_id)
            snapshots.append(snapshot)
        return tuple(snapshots)

    def record(self, snapshot: LockfileSnapshot) -> bool:
        if self._existing is None:
            self._existing = {item.snapshot_id: item for item in self.load()}
        previous = self._existing.get(snapshot.snapshot_id)
        if previous is not None:
            if previous != snapshot:
                raise ValueError(f"snapshot ledger identity collision: {snapshot.snapshot_id}")
            return False
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot.as_json(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        self._existing[snapshot.snapshot_id] = snapshot
        return True
