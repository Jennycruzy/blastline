"""A deterministic append-only graph projection used for traversal and replay."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .json_types import JsonObject, require_object
from .model import Edge, EdgeType, Node, NodeType
from .timeutil import format_time


class GraphStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.nodes_path = directory / "nodes.jsonl"
        self.edges_path = directory / "edges.jsonl"
        self.runs_path = directory / "runs.jsonl"
        self._nodes_cache: list[Node] | None = None
        self._edges_cache: list[Edge] | None = None
        self._node_index: dict[str, Node] | None = None
        self._outgoing_index: dict[str, list[Edge]] | None = None
        self._incoming_index: dict[str, list[Edge]] | None = None
        self.directory.mkdir(parents=True, exist_ok=True)

    def _read_records(self, path: Path) -> list[JsonObject]:
        if not path.exists():
            return []
        records: list[JsonObject] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"unparseable graph record {path}:{line_number}") from exc
            records.append(require_object(parsed, f"graph record {path}:{line_number}"))
        return records

    def nodes(self) -> list[Node]:
        if self._nodes_cache is None:
            self._nodes_cache = [Node.from_json(record) for record in self._read_records(self.nodes_path)]
            self._node_index = {node.node_id: node for node in self._nodes_cache}
        return list(self._nodes_cache)

    def edges(self) -> list[Edge]:
        if self._edges_cache is None:
            self._edges_cache = [Edge.from_json(record) for record in self._read_records(self.edges_path)]
            self._outgoing_index = {}
            self._incoming_index = {}
            for edge in self._edges_cache:
                self._outgoing_index.setdefault(edge.source_id, []).append(edge)
                self._incoming_index.setdefault(edge.target_id, []).append(edge)
        return list(self._edges_cache)

    def _append_unique(self, path: Path, records: Iterable[JsonObject], existing_ids: set[str]) -> list[JsonObject]:
        new_lines: list[str] = []
        added: list[JsonObject] = []
        for record in records:
            record_id = record.get("id")
            if not isinstance(record_id, str):
                raise ValueError(f"record for {path} has no string id")
            if record_id in existing_ids:
                continue
            new_lines.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
            existing_ids.add(record_id)
            added.append(record)
        if new_lines:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(new_lines))
                handle.write("\n")
        return added

    def add_nodes(self, nodes: Iterable[Node]) -> int:
        materialized = list(nodes)
        self.nodes()
        if self._node_index is None or self._nodes_cache is None:
            raise RuntimeError("node indexes were not initialized")
        existing_ids = set(self._node_index)
        records = self._append_unique(self.nodes_path, (node.as_json() for node in materialized), existing_ids)
        for record in records:
            node = Node.from_json(record)
            self._nodes_cache.append(node)
            self._node_index[node.node_id] = node
        return len(records)

    def add_edges(self, edges: Iterable[Edge]) -> int:
        materialized = list(edges)
        self.edges()
        if self._edges_cache is None or self._outgoing_index is None or self._incoming_index is None:
            raise RuntimeError("edge indexes were not initialized")
        existing_ids = {edge.edge_id for edge in self._edges_cache}
        records = self._append_unique(self.edges_path, (edge.as_json() for edge in materialized), existing_ids)
        for record in records:
            edge = Edge.from_json(record)
            self._edges_cache.append(edge)
            self._outgoing_index.setdefault(edge.source_id, []).append(edge)
            self._incoming_index.setdefault(edge.target_id, []).append(edge)
        return len(records)

    def record_run(self, run: JsonObject) -> None:
        payload = dict(run)
        payload["recorded_at"] = format_time(datetime.now().astimezone())
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            handle.write("\n")

    def fingerprint(self) -> str:
        stable = {
            "nodes": sorted((node.as_json() for node in self.nodes()), key=lambda item: str(item["id"])),
            "edges": sorted((edge.as_json() for edge in self.edges()), key=lambda item: str(item["id"])),
        }
        encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def node(self, node_id: str) -> Node | None:
        if self._nodes_cache is None:
            self.nodes()
        if self._node_index is None:
            raise RuntimeError("node index was not initialized")
        return self._node_index.get(node_id)

    def nodes_of_type(self, node_type: NodeType) -> list[Node]:
        return [item for item in self.nodes() if item.node_type is node_type]

    def visible_edges(
        self,
        valid_at: datetime | None = None,
        commit_at: datetime | None = None,
        edge_type: EdgeType | None = None,
    ) -> list[Edge]:
        result: list[Edge] = []
        for edge in self.edges():
            if edge_type is not None and edge.edge_type is not edge_type:
                continue
            if commit_at is not None and edge.commit_at > commit_at:
                continue
            if valid_at is not None and not edge.valid.contains(valid_at):
                continue
            result.append(edge)
        return result

    def outgoing(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
        valid_at: datetime | None = None,
        commit_at: datetime | None = None,
    ) -> list[Edge]:
        if self._edges_cache is None:
            self.edges()
        if self._outgoing_index is None:
            raise RuntimeError("outgoing edge index was not initialized")
        return [
            edge
            for edge in self._outgoing_index.get(node_id, [])
            if (edge_type is None or edge.edge_type is edge_type)
            and (commit_at is None or edge.commit_at <= commit_at)
            and (valid_at is None or edge.valid.contains(valid_at))
        ]

    def incoming(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
        valid_at: datetime | None = None,
        commit_at: datetime | None = None,
    ) -> list[Edge]:
        if self._edges_cache is None:
            self.edges()
        if self._incoming_index is None:
            raise RuntimeError("incoming edge index was not initialized")
        return [
            edge
            for edge in self._incoming_index.get(node_id, [])
            if (edge_type is None or edge.edge_type is edge_type)
            and (commit_at is None or edge.commit_at <= commit_at)
            and (valid_at is None or edge.valid.contains(valid_at))
        ]
