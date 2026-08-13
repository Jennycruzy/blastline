"""Strict parsers for npm and PyPI JSON documents."""

from __future__ import annotations

import json
import re
from datetime import datetime

from ..json_types import JsonObject, JsonValue, require_object, require_string
from ..timeutil import parse_time
from .records import DependencySpec, ParseIssue, RegistryPackage, RegistryVersion


def parse_json_object(body: bytes, source: str) -> JsonObject:
    try:
        raw: JsonValue = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} is invalid JSON") from exc
    return require_object(raw, source)


def parse_dependency_object(value: JsonValue, context: str) -> tuple[DependencySpec, ...]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    dependencies: list[DependencySpec] = []
    for name, requirement in sorted(value.items()):
        if not isinstance(requirement, str):
            raise ValueError(f"{context}.{name} requirement must be a string")
        dependencies.append(DependencySpec(name=name, requirement=requirement))
    return tuple(dependencies)


def parse_npm(body: bytes, source: str) -> tuple[RegistryPackage, tuple[ParseIssue, ...]]:
    document = parse_json_object(body, source)
    name = document.get("name")
    versions_value = document.get("versions")
    time_value = document.get("time")
    if not isinstance(name, str) or not isinstance(versions_value, dict) or not isinstance(time_value, dict):
        raise ValueError(f"{source} lacks npm name, versions, or time object")
    versions: list[RegistryVersion] = []
    issues: list[ParseIssue] = []
    package_modified = time_value.get("modified")
    package_modified_at = parse_time(package_modified, f"{source}.time.modified") if isinstance(package_modified, str) else None
    package_maintainers = parse_npm_maintainers(document.get("maintainers"), f"{source}.maintainers")
    for version_name, version_value in sorted(versions_value.items()):
        identifier = f"{name}@{version_name}"
        try:
            version_object = require_object(version_value, f"{source}.versions.{version_name}")
            published_value = time_value.get(version_name)
            if not isinstance(published_value, str):
                raise ValueError("missing publish timestamp")
            published_at = parse_time(published_value, f"{identifier}.published_at")
            modified_at = package_modified_at if package_modified_at is not None else published_at
            dependencies = parse_dependency_object(version_object.get("dependencies", {}), f"{identifier}.dependencies")
            maintainers = parse_npm_maintainers(version_object.get("maintainers"), f"{identifier}.maintainers")
            if not maintainers:
                maintainers = package_maintainers
            versions.append(
                RegistryVersion(
                    registry="npm",
                    package_name=name,
                    version=version_name,
                    published_at=published_at,
                    modified_at=modified_at,
                    dependencies=dependencies,
                    maintainers=maintainers,
                    yanked=bool(version_object.get("deprecated")),
                    source_identifier=identifier,
                )
            )
        except (TypeError, ValueError) as exc:
            issues.append(ParseIssue("npm", identifier, str(exc)))
    return RegistryPackage("npm", name, tuple(versions), name), tuple(issues)


def parse_npm_maintainers(value: JsonValue | None, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    names: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            names.append(item)
            continue
        if not isinstance(item, dict):
            raise ValueError(f"{context}[{index}] must be an object or string")
        maintainer_name = item.get("name")
        if isinstance(maintainer_name, str):
            names.append(maintainer_name)
        else:
            raise ValueError(f"{context}[{index}] has no maintainer name")
    return tuple(sorted(set(names)))


def parse_pypi(body: bytes, source: str) -> tuple[RegistryPackage, tuple[ParseIssue, ...]]:
    document = parse_json_object(body, source)
    info = document.get("info")
    releases = document.get("releases")
    if not isinstance(info, dict) or not isinstance(releases, dict):
        raise ValueError(f"{source} lacks PyPI info or releases object")
    package_name_value = info.get("name")
    if not isinstance(package_name_value, str):
        raise ValueError(f"{source}.info.name is missing")
    package_name = package_name_value
    maintainers = parse_pypi_maintainers(info)
    versions: list[RegistryVersion] = []
    issues: list[ParseIssue] = []
    for version_name, files_value in sorted(releases.items()):
        identifier = f"{package_name}@{version_name}"
        try:
            if not isinstance(files_value, list) or not files_value:
                raise ValueError("release has no file records")
            file_records: list[JsonObject] = []
            for index, item in enumerate(files_value):
                file_records.append(require_object(item, f"{identifier}.files[{index}]"))
            timestamps = [
                parse_time(value, f"{identifier}.upload_time")
                for record in file_records
                if isinstance(value := record.get("upload_time_iso_8601"), str)
            ]
            if not timestamps:
                raise ValueError("release has no upload timestamp")
            published_at = min(timestamps)
            requirement_value = info.get("requires_dist")
            dependencies = parse_pypi_dependencies(requirement_value, f"{source}.info.requires_dist")
            versions.append(
                RegistryVersion(
                    registry="pypi",
                    package_name=package_name,
                    version=version_name,
                    published_at=published_at,
                    modified_at=max(timestamps),
                    dependencies=dependencies,
                    maintainers=maintainers,
                    yanked=all(bool(record.get("yanked")) for record in file_records),
                    source_identifier=identifier,
                )
            )
        except (TypeError, ValueError) as exc:
            issues.append(ParseIssue("pypi", identifier, str(exc)))
    return RegistryPackage("pypi", package_name, tuple(versions), package_name), tuple(issues)


def parse_pypi_maintainers(info: JsonObject) -> tuple[str, ...]:
    candidates: list[str] = []
    for field in ("maintainer", "author"):
        value = info.get(field)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    return tuple(sorted(set(candidates)))


_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def parse_pypi_dependencies(value: JsonValue | None, context: str) -> tuple[DependencySpec, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    dependencies: list[DependencySpec] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{context}[{index}] must be a string")
        match = _REQUIREMENT_NAME.match(item)
        if match is None:
            raise ValueError(f"{context}[{index}] has no package name")
        dependencies.append(DependencySpec(match.group(1), item[match.end(1) :].strip()))
    return tuple(dependencies)
