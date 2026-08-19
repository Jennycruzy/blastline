"""Graph-free observations derived from cached raw lockfile snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from ..errors import ExternalCallError
from ..ingest.http import DiskHttpClient
from ..ingest.lockfiles import LockfileResult, parse_lockfile
from ..ingest.snapshots import LockfileSnapshot, SnapshotLedger
from ..model import TimeInterval


@dataclass(frozen=True, slots=True)
class LockfileObservation:
    repositories: tuple[str, ...]
    abstentions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SnapshotTargetObservation:
    snapshot: LockfileSnapshot
    registry: str
    package: str
    version: str


class CachedLockfileOracle:
    """Reparse raw cached snapshots without reading graph Resolution edges."""

    def __init__(self, ledger: SnapshotLedger, http: DiskHttpClient) -> None:
        self.snapshots = ledger.load()
        self.http = http
        self._parsed: dict[str, LockfileResult] = {}
        self._parse_failures: dict[str, str] = {}

    def discover_targets(
        self,
        targets: set[tuple[str, str, str]],
    ) -> tuple[SnapshotTargetObservation, ...]:
        """Find target versions directly in raw snapshots, without graph resolution edges."""

        observations: list[SnapshotTargetObservation] = []
        for snapshot in sorted(self.snapshots, key=lambda item: (item.committed_at, item.snapshot_id)):
            parse_abstentions: list[str] = []
            result = self._parse(snapshot, parse_abstentions)
            if result is None:
                continue
            found = {
                (resolution.ecosystem, resolution.package_name, resolution.version)
                for resolution in result.resolutions
                if (resolution.ecosystem, resolution.package_name, resolution.version) in targets
            }
            observations.extend(
                SnapshotTargetObservation(snapshot, registry, package, version)
                for registry, package, version in sorted(found)
            )
        return tuple(observations)

    def observe(
        self,
        registry: str,
        package: str,
        version: str,
        window: tuple[datetime, datetime],
        known_at: datetime,
    ) -> LockfileObservation:
        target_interval = TimeInterval(window[0], window[1])
        candidates = tuple(
            snapshot
            for snapshot in self.snapshots
            if snapshot.ecosystem == registry
            and snapshot.committed_at <= known_at
            and TimeInterval(snapshot.committed_at, snapshot.valid_to).intersects(target_interval)
        )
        if not candidates:
            return LockfileObservation(
                (),
                (
                    f"no cached {registry} lockfile snapshot intersects "
                    f"the verification window for {package}@{version}",
                ),
            )

        repositories: set[str] = set()
        abstentions: list[str] = []
        for snapshot in candidates:
            result = self._parse(snapshot, abstentions)
            if result is None:
                continue
            found = any(
                resolution.ecosystem == registry
                and resolution.package_name == package
                and resolution.version == version
                for resolution in result.resolutions
            )
            if found:
                repositories.add(snapshot.repository)
                continue
            if result.issues:
                abstentions.append(
                    f"{snapshot.snapshot_id}: target resolution could not be established "
                    f"because the snapshot has {len(result.issues)} parse issue(s)"
                )

        return LockfileObservation(tuple(sorted(repositories)), tuple(dict.fromkeys(abstentions)))

    def _parse(self, snapshot: LockfileSnapshot, abstentions: list[str]) -> LockfileResult | None:
        cached_failure = self._parse_failures.get(snapshot.snapshot_id)
        if cached_failure is not None:
            abstentions.append(cached_failure)
            return None
        cached_result = self._parsed.get(snapshot.snapshot_id)
        if cached_result is not None:
            return cached_result
        try:
            response = self.http.read_cached(snapshot.raw_url)
            if response.status < 200 or response.status >= 300:
                raise ExternalCallError(f"cached response status was {response.status}")
            actual_hash = hashlib.sha256(response.body).hexdigest()
            if actual_hash != snapshot.payload_hash:
                raise ExternalCallError(
                    f"cached payload hash changed: expected {snapshot.payload_hash}, got {actual_hash}"
                )
            result = parse_lockfile(snapshot.path, response.body, snapshot.ecosystem)
        except (ExternalCallError, TypeError, UnicodeDecodeError, ValueError) as exc:
            reason = f"{snapshot.snapshot_id}: cached lockfile oracle failed: {exc}"
            self._parse_failures[snapshot.snapshot_id] = reason
            abstentions.append(reason)
            return None
        self._parsed[snapshot.snapshot_id] = result
        return result
