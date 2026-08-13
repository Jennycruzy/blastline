"""Q1-Q8 graph traversals over the append-only local Hydra projection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..config import Settings
from ..errors import Abstention
from ..json_types import JsonObject
from ..model import Edge, EdgeType, Node, NodeType, package_id, version_id
from ..store import GraphStore
from ..timeutil import format_time, now_utc, parse_time
from .types import AbstentionNotice, Coverage, QueryResponse


@dataclass(frozen=True, slots=True)
class RepositoryObservation:
    repository_id: str
    resolution_id: str
    version_id: str
    interval_start: datetime
    interval_end: datetime | None
    lock_path: str


class QueryEngine:
    def __init__(self, store: GraphStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.max_depth = settings.integer("graph", "max_traversal_depth")
        if self.max_depth < 1:
            raise ValueError("graph.max_traversal_depth must be positive")
        self.current_query_epsilon_microseconds = settings.integer("graph", "current_query_epsilon_microseconds")
        if self.current_query_epsilon_microseconds < 1:
            raise ValueError("graph.current_query_epsilon_microseconds must be positive")

    def coverage(self) -> Coverage:
        repositories = self.store.nodes_of_type(NodeType.REPOSITORY)
        total = len(repositories)
        resolvable = 0
        unknown: list[str] = []
        for repository in repositories:
            declarations = self.store.outgoing(repository.node_id, EdgeType.DECLARES)
            if not declarations:
                unknown.append(self._node_label(repository.node_id))
                continue
            has_resolved = any(self.store.outgoing(edge.target_id, EdgeType.RESOLVED_TO) for edge in declarations)
            if has_resolved:
                resolvable += 1
            else:
                unknown.append(self._node_label(repository.node_id))
        return Coverage(total, resolvable, total - resolvable, tuple(sorted(unknown)))

    def _response(
        self,
        query: str,
        results: list[JsonObject],
        abstentions: list[AbstentionNotice],
    ) -> QueryResponse:
        return QueryResponse(query, tuple(results), tuple(abstentions), self.coverage())

    def _version_node(self, registry: str, package: str, version: str) -> Node:
        node_id = version_id(registry, package, version)
        node = self.store.node(node_id)
        if node is None:
            raise Abstention(f"version {registry}:{package}@{version} is not in the graph")
        if node.node_type is not NodeType.VERSION:
            raise Abstention(f"node {node_id} is not a Version")
        return node

    def _package_node_for_version(self, node: Node) -> Node:
        registry = node.attributes.get("registry")
        package = node.attributes.get("package")
        if not isinstance(registry, str) or not isinstance(package, str):
            raise Abstention(f"version node {node.node_id} has no registry/package identity")
        package_node = self.store.node(package_id(registry, package))
        if package_node is None:
            raise Abstention(f"package node is missing for {node.node_id}")
        return package_node

    def _node_label(self, node_id: str) -> str:
        node = self.store.node(node_id)
        if node is None:
            return node_id
        if node.node_type is NodeType.VERSION:
            package = node.attributes.get("package")
            version = node.attributes.get("version")
            if isinstance(package, str) and isinstance(version, str):
                return f"{package}@{version}"
        if node.node_type is NodeType.REPOSITORY:
            full_name = node.attributes.get("full_name")
            if isinstance(full_name, str):
                return full_name
        if node.node_type is NodeType.PACKAGE:
            name = node.attributes.get("name")
            if isinstance(name, str):
                return name
        return node_id

    def _repository_observations(
        self,
        version_node_id: str,
        valid_at: datetime | None,
        commit_at: datetime | None,
    ) -> tuple[RepositoryObservation, ...]:
        observations: list[RepositoryObservation] = []
        for resolved_edge in self.store.incoming(version_node_id, EdgeType.RESOLVED_TO, valid_at, commit_at):
            resolution_id_value = resolved_edge.source_id
            for declared_edge in self.store.incoming(resolution_id_value, EdgeType.DECLARES, valid_at, commit_at):
                resolution_node = self.store.node(resolution_id_value)
                lock_path = "unknown"
                if resolution_node is not None:
                    lock_path_value = resolution_node.attributes.get("lock_path")
                    if isinstance(lock_path_value, str):
                        lock_path = lock_path_value
                observations.append(
                    RepositoryObservation(
                        repository_id=declared_edge.source_id,
                        resolution_id=resolution_id_value,
                        version_id=version_node_id,
                        interval_start=resolved_edge.valid.start,
                        interval_end=resolved_edge.valid.end,
                        lock_path=lock_path,
                    )
                )
        return tuple(observations)

    def _reverse_repository_paths(
        self,
        start_package_id: str,
        valid_at: datetime,
        commit_at: datetime | None,
    ) -> tuple[tuple[str, int, tuple[str, ...]], tuple[AbstentionNotice, ...]]:
        queue: deque[tuple[str, int, tuple[str, ...]]] = deque([(start_package_id, 0, (start_package_id,))])
        visited_packages: set[str] = {start_package_id}
        results: list[tuple[str, int, tuple[str, ...]]] = []
        abstentions: list[AbstentionNotice] = []
        while queue:
            current_package, depth, package_path = queue.popleft()
            incoming = self.store.incoming(current_package, EdgeType.DEPENDS_ON, valid_at, commit_at)
            for dependency_edge in incoming:
                dependent_version = self.store.node(dependency_edge.source_id)
                if dependent_version is None or dependent_version.node_type is not NodeType.VERSION:
                    abstentions.append(AbstentionNotice(dependency_edge.edge_id, "dependency edge source Version node is missing"))
                    continue
                version_path = package_path + (dependent_version.node_id,)
                observations = self._repository_observations(dependent_version.node_id, valid_at, commit_at)
                for observation in observations:
                    results.append((observation.repository_id, depth + 1, version_path + (observation.resolution_id, observation.repository_id)))
                if depth + 1 >= self.max_depth:
                    abstentions.append(
                        AbstentionNotice(
                            dependent_version.node_id,
                            f"reverse dependency closure reached configured depth cap {self.max_depth}",
                        )
                    )
                    continue
                package = dependent_version.attributes.get("package")
                registry = dependent_version.attributes.get("registry")
                if not isinstance(package, str) or not isinstance(registry, str):
                    abstentions.append(AbstentionNotice(dependent_version.node_id, "dependent Version lacks package identity"))
                    continue
                next_package_id = package_id(registry, package)
                if next_package_id not in visited_packages:
                    visited_packages.add(next_package_id)
                    queue.append((next_package_id, depth + 1, package_path + (dependent_version.node_id, next_package_id)))
        return tuple(results), tuple(abstentions)

    def blast_radius(
        self,
        registry: str,
        package: str,
        version: str,
        valid_at: datetime | None = None,
        commit_at: datetime | None = None,
    ) -> QueryResponse:
        observed_at = valid_at if valid_at is not None else now_utc()
        abstentions: list[AbstentionNotice] = []
        try:
            version_node = self._version_node(registry, package, version)
            package_node = self._package_node_for_version(version_node)
            paths, closure_abstentions = self._reverse_repository_paths(package_node.node_id, observed_at, commit_at)
            abstentions.extend(closure_abstentions)
            direct_paths = tuple(
                (
                    observation.repository_id,
                    0,
                    (version_node.node_id, observation.resolution_id, observation.repository_id),
                )
                for observation in self._repository_observations(version_node.node_id, observed_at, commit_at)
            )
            paths = paths + direct_paths
        except Abstention as exc:
            abstentions.append(AbstentionNotice(f"{registry}:{package}@{version}", str(exc)))
            paths = ()
        results: list[JsonObject] = []
        unique: set[tuple[str, tuple[str, ...]]] = set()
        for repository_id_value, depth, path in paths:
            key = (repository_id_value, path)
            if key in unique:
                continue
            unique.add(key)
            results.append(
                {
                    "repository": self._node_label(repository_id_value),
                    "depth": depth,
                    "path": [self._node_label(item) for item in path],
                    "valid_at": format_time(observed_at),
                }
            )
        if not results and not abstentions:
            abstentions.append(AbstentionNotice(f"{registry}:{package}@{version}", "no repository path is supported by the observed graph at this time"))
        return self._response(f"Q1 blast radius {registry}:{package}@{version}", results, abstentions)

    def window_exposure(
        self,
        registry: str,
        package: str,
        version: str,
        window: tuple[datetime, datetime],
        known_at: datetime | None = None,
    ) -> QueryResponse:
        start, end = window
        if end <= start:
            raise ValueError("exposure window must end after start")
        abstentions: list[AbstentionNotice] = []
        results: list[JsonObject] = []
        try:
            version_node = self._version_node(registry, package, version)
        except Abstention as exc:
            abstentions.append(AbstentionNotice(f"{registry}:{package}@{version}", str(exc)))
            return self._response(f"Q3 window exposure {registry}:{package}@{version}", results, abstentions)
        target_interval = self._interval(start, end)
        for resolved_edge in self.store.incoming(version_node.node_id, EdgeType.RESOLVED_TO, commit_at=known_at):
            if not resolved_edge.valid.intersects(target_interval):
                continue
            for declared_edge in self.store.incoming(resolved_edge.source_id, EdgeType.DECLARES, commit_at=known_at):
                overlap_start = max(start, resolved_edge.valid.start)
                overlap_end = min_datetime(end, resolved_edge.valid.end)
                resolution_node = self.store.node(resolved_edge.source_id)
                lock_path = "unknown"
                if resolution_node is not None and isinstance(resolution_node.attributes.get("lock_path"), str):
                    lock_path = str(resolution_node.attributes["lock_path"])
                results.append(
                    {
                        "repository": self._node_label(declared_edge.source_id),
                        "resolution": resolved_edge.source_id,
                        "lock_path": lock_path,
                        "valid_overlap": {
                            "start": format_time(overlap_start),
                            "end": format_time(overlap_end) if overlap_end is not None else None,
                        },
                        "edge_commit_at": format_time(resolved_edge.commit_at),
                    }
                )
        if not results:
            abstentions.append(AbstentionNotice(f"{registry}:{package}@{version}", "no observed Resolution interval intersects the requested window"))
        return self._response(f"Q3 window exposure {registry}:{package}@{version}", dedupe_objects(results), abstentions)

    def current_exposure(self, registry: str, package: str, version: str, as_of: datetime | None = None) -> QueryResponse:
        instant = as_of if as_of is not None else now_utc()
        return self.window_exposure(
            registry,
            package,
            version,
            (instant, instant + timedelta(microseconds=self.current_query_epsilon_microseconds)),
            known_at=instant,
        )

    def first_affected_version(
        self,
        registry: str,
        package: str,
        version: str,
        known_at: datetime | None = None,
    ) -> QueryResponse:
        abstentions: list[AbstentionNotice] = []
        try:
            target = self._version_node(registry, package, version)
        except Abstention as exc:
            return self._response(f"Q2 first affected version {registry}:{package}@{version}", [], [AbstentionNotice(version, str(exc))])
        advisory_edges = self.store.incoming(target.node_id, EdgeType.AFFECTS, commit_at=known_at)
        if not advisory_edges:
            abstentions.append(AbstentionNotice(target.node_id, "no OSV AFFECTS edge is available for this version"))
            return self._response(f"Q2 first affected version {registry}:{package}@{version}", [], abstentions)
        candidates_by_id: dict[str, Node] = {}
        for advisory_edge in advisory_edges:
            for affected_edge in self.store.outgoing(advisory_edge.source_id, EdgeType.AFFECTS, commit_at=known_at):
                affected_node = self.store.node(affected_edge.target_id)
                if affected_node is None or affected_node.node_type is not NodeType.VERSION:
                    abstentions.append(AbstentionNotice(affected_edge.edge_id, "advisory target Version node is missing"))
                    continue
                if affected_node.attributes.get("package") == package and affected_node.attributes.get("registry") == registry:
                    candidates_by_id[affected_node.node_id] = affected_node
        candidates = list(candidates_by_id.values())
        candidates.sort(key=lambda item: str(item.attributes.get("published_at", "")))
        if not candidates:
            abstentions.append(AbstentionNotice(target.node_id, "advisory exists but no earlier affected version is present in the graph"))
            return self._response(f"Q2 first affected version {registry}:{package}@{version}", [], abstentions)
        first = candidates[0]
        result: JsonObject = {
            "requested_version": version,
            "first_affected_version": str(first.attributes.get("version", "unknown")),
            "published_at": str(first.attributes.get("published_at", "unknown")),
            "chain_length": len(candidates),
            "versions_in_advisory": [str(item.attributes.get("version", "unknown")) for item in candidates],
        }
        return self._response(f"Q2 first affected version {registry}:{package}@{version}", [result], abstentions)

    def maintainer_risk(self, maintainer: str, valid_at: datetime | None = None) -> QueryResponse:
        instant = valid_at if valid_at is not None else now_utc()
        candidates = [node for node in self.store.nodes_of_type(NodeType.MAINTAINER) if node.node_id == maintainer or node.attributes.get("name") == maintainer]
        abstentions: list[AbstentionNotice] = []
        results: list[JsonObject] = []
        if not candidates:
            abstentions.append(AbstentionNotice(maintainer, "maintainer is not in the graph"))
        for maintainer_node in candidates:
            package_edges = self.store.outgoing(maintainer_node.node_id, EdgeType.MAINTAINS, valid_at=instant)
            seen_packages: set[str] = set()
            for package_edge in package_edges:
                if package_edge.target_id in seen_packages:
                    continue
                seen_packages.add(package_edge.target_id)
                paths, closure_abstentions = self._reverse_repository_paths(package_edge.target_id, instant, None)
                abstentions.extend(closure_abstentions)
                repositories = sorted({self._node_label(item[0]) for item in paths})
                results.append(
                    {
                        "maintainer": self._node_label(maintainer_node.node_id),
                        "package": self._node_label(package_edge.target_id),
                        "publish_rights_valid_at": format_time(instant),
                        "transitive_repositories": repositories,
                        "repository_count": len(repositories),
                    }
                )
        if not results and not abstentions:
            abstentions.append(AbstentionNotice(maintainer, "no MAINTAINS edge is valid at the requested time"))
        return self._response(f"Q4 credential blast radius {maintainer}", results, abstentions)

    def shared_infrastructure(self, registry: str, package: str, version: str, valid_at: datetime | None = None) -> QueryResponse:
        instant = valid_at if valid_at is not None else now_utc()
        abstentions: list[AbstentionNotice] = []
        grouped: dict[tuple[str, str, str], set[str]] = {}
        try:
            target = self._version_node(registry, package, version)
        except Abstention as exc:
            return self._response(f"Q5 shared infrastructure {registry}:{package}@{version}", [], [AbstentionNotice(version, str(exc))])
        keys: set[tuple[str, str]] = set()
        for edge_type in (EdgeType.PUBLISHED_FROM, EdgeType.PUBLISHED_BY):
            for edge in self.store.outgoing(target.node_id, edge_type, valid_at=instant):
                keys.add((edge_type.value, edge.target_id))
                for shared_edge in self.store.incoming(edge.target_id, edge_type, valid_at=instant):
                    if shared_edge.source_id == target.node_id:
                        continue
                    other = self.store.node(shared_edge.source_id)
                    if other is None or other.node_type is not NodeType.VERSION:
                        abstentions.append(AbstentionNotice(shared_edge.edge_id, "shared infrastructure target Version is missing"))
                        continue
                    other_package = other.attributes.get("package")
                    other_version = other.attributes.get("version")
                    if isinstance(other_package, str) and isinstance(other_version, str) and other_package != package:
                        grouped.setdefault((edge_type.value, edge.target_id, other_package), set()).add(other_version)
        results: list[JsonObject] = [
            {
                "shared_by": edge_type,
                "shared_identifier": shared_identifier,
                "package": other_package,
                "versions": sorted(versions),
                "version_count": len(versions),
            }
            for (edge_type, shared_identifier, other_package), versions in sorted(grouped.items())
        ]
        if not results and not keys:
            abstentions.append(AbstentionNotice(target.node_id, "compromised Version has no publish maintainer or infrastructure edge"))
        elif not results:
            abstentions.append(AbstentionNotice(target.node_id, "shared publisher/infrastructure edge exists, but no distinct package has corroborating Version evidence"))
        return self._response(f"Q5 shared infrastructure {registry}:{package}@{version}", dedupe_objects(results), abstentions)

    def still_dirty(
        self,
        registry: str,
        package: str,
        version: str,
        window: tuple[datetime, datetime],
        as_of: datetime | None = None,
    ) -> QueryResponse:
        instant = as_of if as_of is not None else now_utc()
        historical = self.window_exposure(registry, package, version, window, known_at=instant)
        abstentions = list(historical.abstentions)
        target_id = version_id(registry, package, version)
        current_repositories: dict[str, set[str]] = {}
        for edge in self.store.incoming(target_id, EdgeType.RESOLVED_TO, instant, instant):
            for declared in self.store.incoming(edge.source_id, EdgeType.DECLARES, instant, instant):
                current_repositories.setdefault(declared.source_id, set()).add(target_id)
        historic: dict[str, JsonObject] = {}
        for result in historical.results:
            repository_value = result.get("repository")
            if isinstance(repository_value, str):
                historic[repository_value] = result
        results: list[JsonObject] = []
        for repository, result in sorted(historic.items()):
            if repository not in current_repositories:
                current_versions = self._current_versions_for_repository(repository, instant, registry, package)
                results.append(
                    {
                        "repository": repository,
                        "historical_exposure": result,
                        "current_lockfile_versions": current_versions,
                        "status": "still-dirty-candidate",
                    }
                )
        if not results and not abstentions:
            abstentions.append(AbstentionNotice(f"{registry}:{package}@{version}", "no repository was both historically exposed and currently resolved clean"))
        return self._response(f"Q7 still dirty {registry}:{package}@{version}", results, abstentions)

    def _current_versions_for_repository(
        self,
        label: str,
        instant: datetime,
        registry: str | None = None,
        package: str | None = None,
    ) -> list[str]:
        repository_nodes = [
            node
            for node in self.store.nodes_of_type(NodeType.REPOSITORY)
            if self._node_label(node.node_id) == label or node.node_id == label
        ]
        versions: list[str] = []
        for repository in repository_nodes:
            for declaration in self.store.outgoing(repository.node_id, EdgeType.DECLARES, instant, instant):
                for resolved in self.store.outgoing(declaration.target_id, EdgeType.RESOLVED_TO, instant, instant):
                    node = self.store.node(resolved.target_id)
                    if node is not None:
                        pkg = node.attributes.get("package")
                        ver = node.attributes.get("version")
                        node_registry = node.attributes.get("registry")
                        if isinstance(pkg, str) and isinstance(ver, str) and (registry is None or node_registry == registry) and (package is None or pkg == package):
                            versions.append(f"{pkg}@{ver}")
        return sorted(set(versions))

    def coverage_report(self) -> QueryResponse:
        coverage = self.coverage()
        return QueryResponse("Q8 abstention and coverage", (), (), coverage)

    @staticmethod
    def _interval(start: datetime, end: datetime):
        from ..model import TimeInterval

        return TimeInterval(start, end)


def min_datetime(left: datetime, right: datetime | None) -> datetime | None:
    if right is None:
        return left
    return min(left, right)


def dedupe_objects(objects: list[JsonObject]) -> list[JsonObject]:
    unique: dict[str, JsonObject] = {}
    import json

    for item in objects:
        key = json.dumps(item, sort_keys=True, separators=(",", ":"))
        unique[key] = item
    return list(unique.values())
