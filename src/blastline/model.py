"""Typed graph records and bitemporal edge semantics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .json_types import JsonObject, JsonValue
from .timeutil import format_time, parse_time


class NodeType(StrEnum):
    PACKAGE = "Package"
    VERSION = "Version"
    MAINTAINER = "Maintainer"
    REPOSITORY = "Repository"
    RESOLUTION = "Resolution"
    ADVISORY = "Advisory"
    PUBLISH_INFRA = "PublishInfra"


class EdgeType(StrEnum):
    DEPENDS_ON = "DEPENDS_ON"
    RESOLVED_TO = "RESOLVED_TO"
    DECLARES = "DECLARES"
    PUBLISHED_BY = "PUBLISHED_BY"
    MAINTAINS = "MAINTAINS"
    PUBLISHED_FROM = "PUBLISHED_FROM"
    AFFECTS = "AFFECTS"
    SIMILAR_NAME_TO = "SIMILAR_NAME_TO"


@dataclass(frozen=True, slots=True)
class TimeInterval:
    start: datetime
    end: datetime | None = None

    def __post_init__(self) -> None:
        if self.end is not None and self.end <= self.start:
            raise ValueError("time interval end must be after start")

    def contains(self, instant: datetime) -> bool:
        if instant < self.start:
            return False
        if self.end is not None and instant >= self.end:
            return False
        return True

    def intersects(self, other: "TimeInterval") -> bool:
        if self.end is not None and self.end <= other.start:
            return False
        if other.end is not None and other.end <= self.start:
            return False
        return True

    def as_json(self) -> JsonObject:
        result: JsonObject = {"start": format_time(self.start)}
        if self.end is not None:
            result["end"] = format_time(self.end)
        return result


@dataclass(frozen=True, slots=True)
class Node:
    node_id: str
    node_type: NodeType
    attributes: JsonObject = field(default_factory=dict)

    def as_json(self) -> JsonObject:
        return {
            "id": self.node_id,
            "type": self.node_type.value,
            "attributes": self.attributes,
        }

    @classmethod
    def from_json(cls, value: JsonObject) -> "Node":
        node_id = value.get("id")
        node_type = value.get("type")
        attributes = value.get("attributes")
        if not isinstance(node_id, str) or not isinstance(node_type, str):
            raise ValueError("node requires string id and type")
        if not isinstance(attributes, dict):
            raise ValueError("node attributes must be an object")
        try:
            parsed_type = NodeType(node_type)
        except ValueError as exc:
            raise ValueError(f"unknown node type: {node_type}") from exc
        return cls(node_id=node_id, node_type=parsed_type, attributes=attributes)


@dataclass(frozen=True, slots=True)
class Edge:
    edge_id: str
    source_id: str
    edge_type: EdgeType
    target_id: str
    valid: TimeInterval
    commit_at: datetime
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        source_id: str,
        edge_type: EdgeType,
        target_id: str,
        valid: TimeInterval,
        commit_at: datetime,
        metadata: JsonObject | None = None,
    ) -> "Edge":
        body: JsonObject = {
            "source_id": source_id,
            "edge_type": edge_type.value,
            "target_id": target_id,
            "valid": valid.as_json(),
            "commit_at": format_time(commit_at),
            "metadata": metadata if metadata is not None else {},
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        edge_id = hashlib.sha256(encoded).hexdigest()
        return cls(
            edge_id=edge_id,
            source_id=source_id,
            edge_type=edge_type,
            target_id=target_id,
            valid=valid,
            commit_at=commit_at,
            metadata=metadata if metadata is not None else {},
        )

    def as_json(self) -> JsonObject:
        return {
            "id": self.edge_id,
            "source_id": self.source_id,
            "type": self.edge_type.value,
            "target_id": self.target_id,
            "valid": self.valid.as_json(),
            "commit_at": format_time(self.commit_at),
            "metadata": self.metadata,
        }

    @classmethod
    def from_json(cls, value: JsonObject) -> "Edge":
        edge_id = value.get("id")
        source_id = value.get("source_id")
        edge_type = value.get("type")
        target_id = value.get("target_id")
        valid = value.get("valid")
        commit_at = value.get("commit_at")
        metadata = value.get("metadata")
        if not all(isinstance(item, str) for item in (edge_id, source_id, edge_type, target_id, commit_at)):
            raise ValueError("edge requires string identity and commit fields")
        if not isinstance(valid, dict) or not isinstance(metadata, dict):
            raise ValueError("edge valid and metadata fields must be objects")
        valid_start = valid.get("start")
        valid_end = valid.get("end")
        if not isinstance(valid_start, str):
            raise ValueError("edge valid.start must be a timestamp")
        try:
            parsed_type = EdgeType(edge_type)
        except ValueError as exc:
            raise ValueError(f"unknown edge type: {edge_type}") from exc
        return cls(
            edge_id=edge_id,
            source_id=source_id,
            edge_type=parsed_type,
            target_id=target_id,
            valid=TimeInterval(
                parse_time(valid_start, "edge valid.start"),
                parse_time(valid_end, "edge valid.end") if isinstance(valid_end, str) else None,
            ),
            commit_at=parse_time(commit_at, "edge commit_at"),
            metadata=metadata,
        )


def package_id(registry: str, name: str) -> str:
    return f"package:{registry}:{name}"


def version_id(registry: str, name: str, version: str) -> str:
    return f"version:{registry}:{name}@{version}"


def repository_id(host: str, full_name: str) -> str:
    return f"repository:{host}:{full_name}"


def resolution_id(repository: str, package: str, version: str, start: datetime) -> str:
    material = f"{repository}|{package}|{version}|{format_time(start)}"
    return f"resolution:{hashlib.sha256(material.encode()).hexdigest()}"
