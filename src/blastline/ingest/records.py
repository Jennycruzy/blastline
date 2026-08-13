"""Normalized registry records before they become graph nodes and edges."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DependencySpec:
    name: str
    requirement: str


@dataclass(frozen=True, slots=True)
class RegistryVersion:
    registry: str
    package_name: str
    version: str
    published_at: datetime
    modified_at: datetime
    dependencies: tuple[DependencySpec, ...] = ()
    maintainers: tuple[str, ...] = ()
    yanked: bool = False
    source_identifier: str = ""


@dataclass(frozen=True, slots=True)
class RegistryPackage:
    registry: str
    name: str
    versions: tuple[RegistryVersion, ...]
    source_identifier: str


@dataclass(frozen=True, slots=True)
class ParseIssue:
    source: str
    identifier: str
    reason: str
