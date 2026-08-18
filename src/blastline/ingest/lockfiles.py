"""Strict lockfile resolution for common npm and Python formats."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from ..json_types import JsonObject, JsonValue, require_object
from ..model import Edge, EdgeType, Node, NodeType, TimeInterval, package_id, repository_id, resolution_id, version_id


@dataclass(frozen=True, slots=True)
class LockDependency:
    name: str
    requirement: str


@dataclass(frozen=True, slots=True)
class LockResolution:
    ecosystem: str
    package_name: str
    version: str
    lock_path: str
    dependencies: tuple[LockDependency, ...]


@dataclass(frozen=True, slots=True)
class LockfileIssue:
    identifier: str
    reason: str


@dataclass(frozen=True, slots=True)
class LockfileResult:
    format: str
    resolutions: tuple[LockResolution, ...]
    issues: tuple[LockfileIssue, ...]


def parse_lockfile(filename: str, body: bytes, ecosystem: str) -> LockfileResult:
    lower_name = PurePosixPath(filename).name.lower()
    if lower_name == "package-lock.json":
        return parse_package_lock(body, ecosystem)
    if lower_name == "yarn.lock":
        return parse_yarn_lock(body, ecosystem)
    if lower_name == "pnpm-lock.yaml":
        return parse_pnpm_lock(body, ecosystem)
    if lower_name == "poetry.lock":
        return parse_poetry_lock(body, ecosystem)
    if lower_name in ("requirements.txt", "requirements.in"):
        return parse_requirements(body, ecosystem)
    raise ValueError(f"unsupported lockfile name: {filename}")


def parse_package_lock(body: bytes, ecosystem: str) -> LockfileResult:
    try:
        raw: JsonValue = json.loads(body)
        document = require_object(raw, "package-lock.json")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("package-lock.json is not valid JSON") from exc
    resolutions: list[LockResolution] = []
    issues: list[LockfileIssue] = []
    packages_value = document.get("packages")
    if isinstance(packages_value, dict):
        for lock_path, record_value in sorted(packages_value.items()):
            if not isinstance(lock_path, str) or not lock_path:
                continue
            if not lock_path.startswith("node_modules/"):
                continue
            try:
                record = require_object(record_value, f"package-lock.json.packages.{lock_path}")
                package_name = package_name_from_node_modules_path(lock_path)
                version_value = record.get("version")
                if not isinstance(version_value, str):
                    raise ValueError("resolved version is missing")
                dependencies = parse_json_dependencies(record.get("dependencies"), lock_path)
                resolutions.append(LockResolution(ecosystem, package_name, version_value, lock_path, dependencies))
            except ValueError as exc:
                issues.append(LockfileIssue(lock_path, str(exc)))
        return LockfileResult("package-lock.json", tuple(resolutions), tuple(issues))
    dependencies_value = document.get("dependencies")
    if not isinstance(dependencies_value, dict):
        raise ValueError("package-lock.json has neither packages nor dependencies")
    walk_package_lock_v1(dependencies_value, "", ecosystem, resolutions, issues)
    return LockfileResult("package-lock.json", tuple(resolutions), tuple(issues))


def walk_package_lock_v1(
    dependencies: dict[str, JsonValue],
    parent_path: str,
    ecosystem: str,
    resolutions: list[LockResolution],
    issues: list[LockfileIssue],
) -> None:
    for package_name, record_value in sorted(dependencies.items()):
        lock_path = f"{parent_path}/node_modules/{package_name}" if parent_path else f"node_modules/{package_name}"
        try:
            record = require_object(record_value, f"package-lock.json.dependencies.{lock_path}")
            version_value = record.get("version")
            if not isinstance(version_value, str):
                raise ValueError("resolved version is missing")
            nested = record.get("dependencies")
            nested_object = nested if isinstance(nested, dict) else {}
            dep_specs = parse_json_dependencies(record.get("requires"), lock_path)
            resolutions.append(LockResolution(ecosystem, package_name, version_value, lock_path, dep_specs))
            walk_package_lock_v1(nested_object, lock_path, ecosystem, resolutions, issues)
        except ValueError as exc:
            issues.append(LockfileIssue(lock_path, str(exc)))


def package_name_from_node_modules_path(lock_path: str) -> str:
    suffix = lock_path.rsplit("node_modules/", 1)[-1]
    if suffix.startswith("@"):
        parts = suffix.split("/")
        if len(parts) < 2:
            raise ValueError(f"scoped package path is incomplete: {lock_path}")
        return "/".join(parts[:2])
    return suffix.split("/", 1)[0]


def parse_json_dependencies(value: JsonValue | None, context: str) -> tuple[LockDependency, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ValueError(f"{context}.dependencies must be an object")
    dependencies: list[LockDependency] = []
    for name, requirement_value in sorted(value.items()):
        if not isinstance(requirement_value, str):
            raise ValueError(f"{context}.dependency {name} has no string requirement")
        dependencies.append(LockDependency(name, requirement_value))
    return tuple(dependencies)


_YARN_KEY = re.compile(r"^(?P<selectors>.+):$")
_YARN_VERSION = re.compile(r'^version\s+"(?P<version>[^"]+)"')
_YARN_DEPENDENCY = re.compile(r'^\s{4}(?P<name>[^\s]+)\s+"?(?P<requirement>[^"\s]+)"?')


def parse_yarn_lock(body: bytes, ecosystem: str) -> LockfileResult:
    lines = body.decode("utf-8").splitlines()
    resolutions: list[LockResolution] = []
    issues: list[LockfileIssue] = []
    selectors: list[str] = []
    version: str | None = None
    dependencies: list[LockDependency] = []
    in_dependencies = False

    def finish() -> None:
        nonlocal selectors, version, dependencies, in_dependencies
        if not selectors:
            return
        if version is None:
            issues.append(LockfileIssue(",".join(selectors), "yarn block has no resolved version"))
        else:
            for selector in selectors:
                resolutions.append(
                    LockResolution(ecosystem, selector_name(selector), version, f"selector:{selector}", tuple(dependencies))
                )
        selectors = []
        version = None
        dependencies = []
        in_dependencies = False

    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if not raw_line.startswith((" ", "\t")) and raw_line.endswith(":"):
            finish()
            match = _YARN_KEY.match(raw_line.strip())
            if match is None:
                raise ValueError(f"cannot parse yarn selector: {raw_line}")
            selectors = [item.strip().strip('"') for item in match.group("selectors").split(",")]
            continue
        stripped = raw_line.strip()
        version_match = _YARN_VERSION.match(stripped)
        if version_match is not None:
            version = version_match.group("version")
            in_dependencies = False
            continue
        if stripped == "dependencies:":
            in_dependencies = True
            continue
        if in_dependencies:
            dependency_match = _YARN_DEPENDENCY.match(raw_line)
            if dependency_match is None:
                if raw_line.startswith("  "):
                    continue
                in_dependencies = False
                continue
            dependencies.append(LockDependency(dependency_match.group("name"), dependency_match.group("requirement")))
    finish()
    if not resolutions and not issues:
        raise ValueError("yarn.lock has no resolvable blocks")
    return LockfileResult("yarn.lock", tuple(resolutions), tuple(issues))


def selector_name(selector: str) -> str:
    candidate = selector.strip().strip('"')
    if candidate.startswith("@"):
        slash = candidate.find("/")
        at = candidate.find("@", slash + 1)
        if slash > 0 and at > slash:
            return candidate[:at]
        return candidate
    at = candidate.find("@")
    if at <= 0:
        raise ValueError(f"cannot identify yarn package in selector: {selector}")
    return candidate[:at]


def parse_pnpm_lock(body: bytes, ecosystem: str) -> LockfileResult:
    lines = body.decode("utf-8").splitlines()
    resolutions: list[LockResolution] = []
    issues: list[LockfileIssue] = []
    section: str | None = None
    current_key: str | None = None
    current_dependencies: list[LockDependency] = []
    in_dependencies = False

    def finish() -> None:
        nonlocal current_key, current_dependencies, in_dependencies
        if current_key is None:
            return
        try:
            package_name, version = pnpm_key_parts(current_key)
            resolutions.append(LockResolution(ecosystem, package_name, version, current_key, tuple(current_dependencies)))
        except ValueError as exc:
            issues.append(LockfileIssue(current_key, str(exc)))
        current_key = None
        current_dependencies = []
        in_dependencies = False

    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if not raw_line.startswith(" ") and raw_line.endswith(":"):
            finish()
            section = raw_line.strip()[:-1]
            continue
        if section not in ("packages", "snapshots"):
            continue
        if raw_line.startswith("  ") and not raw_line.startswith("    ") and raw_line.rstrip().endswith(":"):
            finish()
            current_key = raw_line.strip()[:-1]
            continue
        if current_key is None:
            continue
        stripped = raw_line.strip()
        if stripped == "dependencies:":
            in_dependencies = True
            continue
        if in_dependencies:
            if len(raw_line) - len(raw_line.lstrip(" ")) < 4:
                in_dependencies = False
                continue
            if ":" not in stripped:
                continue
            name, requirement = stripped.split(":", 1)
            name = name.strip().strip('"\'')
            requirement = requirement.strip().strip('"\'')
            if requirement:
                current_dependencies.append(LockDependency(name, requirement))
    finish()
    if not resolutions and not issues:
        raise ValueError("pnpm-lock.yaml has no package snapshots")
    unique = {(item.package_name, item.version, item.lock_path): item for item in resolutions}
    return LockfileResult("pnpm-lock.yaml", tuple(unique.values()), tuple(issues))


def pnpm_key_parts(key: str) -> tuple[str, str]:
    candidate = key.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "'\"":
        candidate = candidate[1:-1]
    candidate = candidate.lstrip("/")

    if not candidate:
        raise ValueError(f"pnpm package key has no version: {key}")

    # pnpm v7+ uses package@version, while older lockfiles use
    # /package/version.  For scoped packages, the version separator is the
    # @ after the scope/name slash, not the leading scope marker.
    if candidate.startswith("@"):
        scope_separator = candidate.find("/")
        path_separator = candidate.find("/", scope_separator + 1)
        package_separator = candidate.find("@", scope_separator + 1)
        if package_separator <= scope_separator or (
            path_separator >= 0 and package_separator > path_separator
        ):
            package_separator = -1
    else:
        path_separator = candidate.find("/")
        package_separator = candidate.find("@")
        if package_separator <= 0 or (path_separator >= 0 and package_separator > path_separator):
            package_separator = -1
    if package_separator > 0:
        package_name = candidate[:package_separator]
        version = candidate[package_separator + 1 :]
    else:
        if candidate.startswith("@"):
            package_separator = candidate.find("/", candidate.find("/") + 1)
        else:
            package_separator = candidate.find("/")
        if package_separator <= 0:
            raise ValueError(f"pnpm package key has no version: {key}")
        package_name = candidate[:package_separator]
        version = candidate[package_separator + 1 :]

    # Peer dependency context is part of the lockfile key, not the package's
    # resolved version: foo@1.0.0(bar@2.0.0) and /foo/1.0.0_bar@2.0.0 both
    # resolve foo at 1.0.0.
    version = re.split(r"[_(]", version, maxsplit=1)[0]
    if not package_name or not version:
        raise ValueError(f"pnpm package key is incomplete: {key}")
    return package_name, version


def parse_poetry_lock(body: bytes, ecosystem: str) -> LockfileResult:
    try:
        document = tomllib.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("poetry.lock is not valid TOML") from exc
    packages_value = document.get("package")
    if not isinstance(packages_value, list):
        raise ValueError("poetry.lock has no package array")
    resolutions: list[LockResolution] = []
    issues: list[LockfileIssue] = []
    for index, item in enumerate(packages_value):
        if not isinstance(item, dict):
            issues.append(LockfileIssue(str(index), "poetry package entry is not a table"))
            continue
        name = item.get("name")
        version = item.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            issues.append(LockfileIssue(str(index), "poetry package lacks name or version"))
            continue
        dependencies_value = item.get("dependencies")
        dependencies: list[LockDependency] = []
        if dependencies_value is not None:
            if not isinstance(dependencies_value, dict):
                issues.append(LockfileIssue(name, "poetry dependencies is not a table"))
                continue
            for dependency_name, requirement_value in sorted(dependencies_value.items()):
                if not isinstance(requirement_value, str):
                    issues.append(LockfileIssue(name, f"dependency {dependency_name} is not a string"))
                    continue
                dependencies.append(LockDependency(dependency_name, requirement_value))
        resolutions.append(LockResolution(ecosystem, name, version, f"package:{name}", tuple(dependencies)))
    return LockfileResult("poetry.lock", tuple(resolutions), tuple(issues))


_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^]]+\])?\s*(===|==)\s*([^;\s]+)")


def parse_requirements(body: bytes, ecosystem: str) -> LockfileResult:
    lines = body.decode("utf-8").splitlines()
    resolutions: list[LockResolution] = []
    issues: list[LockfileIssue] = []
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("-r", "--", "-c")):
            issues.append(LockfileIssue(str(line_number), "include and pip options are not resolved from this file"))
            continue
        match = _REQUIREMENT.match(line)
        if match is None:
            issues.append(LockfileIssue(str(line_number), "requirement is not an exact pinned version"))
            continue
        name = match.group(1)
        version = match.group(3)
        resolutions.append(LockResolution(ecosystem, name, version, f"line:{line_number}", ()))
    if not resolutions and not issues:
        raise ValueError("requirements file has no entries")
    return LockfileResult("requirements.txt", tuple(resolutions), tuple(issues))


def graphify_lockfile(
    host: str,
    full_name: str,
    result: LockfileResult,
    valid_from: datetime,
    valid_to: datetime | None,
) -> tuple[list[Node], list[Edge]]:
    repository_node_id = repository_id(host, full_name)
    interval = TimeInterval(valid_from, valid_to)
    nodes: list[Node] = [
        Node(repository_node_id, NodeType.REPOSITORY, {"host": host, "full_name": full_name}),
    ]
    edges: list[Edge] = []
    for resolution in result.resolutions:
        package_node_id = package_id(resolution.ecosystem, resolution.package_name)
        resolved_version_node_id = version_id(resolution.ecosystem, resolution.package_name, resolution.version)
        resolution_node_id = resolution_id(
            repository_node_id,
            package_node_id,
            resolution.version,
            valid_from,
            resolution.lock_path,
        )
        nodes.extend(
            [
                Node(package_node_id, NodeType.PACKAGE, {"registry": resolution.ecosystem, "name": resolution.package_name}),
                Node(
                    resolved_version_node_id,
                    NodeType.VERSION,
                    {
                        "registry": resolution.ecosystem,
                        "package": resolution.package_name,
                        "version": resolution.version,
                        "observed_from_lockfile": True,
                        "evidence": "parsed-lockfile",
                    },
                ),
                Node(
                    resolution_node_id,
                    NodeType.RESOLUTION,
                    {
                        "repository": repository_node_id,
                        "package": package_node_id,
                        "version": resolution.version,
                        "lock_path": resolution.lock_path,
                        "format": result.format,
                    },
                ),
            ]
        )
        edges.extend(
            [
                Edge.create(repository_node_id, EdgeType.DECLARES, resolution_node_id, interval, valid_from, {"format": result.format, "evidence": "parsed-lockfile"}),
                Edge.create(resolution_node_id, EdgeType.RESOLVED_TO, resolved_version_node_id, interval, valid_from, {"format": result.format, "evidence": "parsed-lockfile"}),
            ]
        )
        for dependency in resolution.dependencies:
            dependency_package_node_id = package_id(resolution.ecosystem, dependency.name)
            nodes.append(
                Node(
                    dependency_package_node_id,
                    NodeType.PACKAGE,
                    {"registry": resolution.ecosystem, "name": dependency.name},
                )
            )
            edges.append(
                Edge.create(
                    resolved_version_node_id,
                    EdgeType.DEPENDS_ON,
                    dependency_package_node_id,
                    interval,
                    valid_from,
                    {"requirement": dependency.requirement, "source": result.format},
                )
            )
    return _deduplicate_nodes(nodes), _deduplicate_edges(edges)


def _deduplicate_nodes(nodes: list[Node]) -> list[Node]:
    values: dict[str, Node] = {}
    for node in nodes:
        existing = values.get(node.node_id)
        if existing is not None and existing != node:
            raise ValueError(f"lockfile node identity collision: {node.node_id}")
        values[node.node_id] = node
    return [values[key] for key in sorted(values)]


def _deduplicate_edges(edges: list[Edge]) -> list[Edge]:
    values = {edge.edge_id: edge for edge in edges}
    return [values[key] for key in sorted(values)]
