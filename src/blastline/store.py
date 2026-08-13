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
        return [Node.from_json(record) for record in self._read_records(self.nodes_path)]

    def edges(self) -> list[Edge]:
        return [Edge.from_json(record) for record in self._read_records(self.edges_path)]

    def _append_unique(self, path: Path, records: Iterable[JsonObject]) -> int:
        existing_ids = {
            record_id
            for record in self._read_records(path)
            if isinstance(record_id := record.get("id"), str)
        }
        new_lines: list[str] = []
        added = 0
        for record in records:
            record_id = record.get("id")
            if not isinstance(record_id, str):
                raise ValueError(f"record for {path} has no string id")
            if record_id in existing_ids:
                continue
            new_lines.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
            existing_ids.add(record_id)
            added += 1
        if new_lines:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(new_lines))
                handle.write("\n")
        return added

    def add_nodes(self, nodes: Iterable[Node]) -> int:
        return self._append_unique(self.nodes_path, (node.as_json() for node in nodes))

    def add_edges(self, edges: Iterable[Edge]) -> int:
        return self._append_unique(self.edges_path, (edge.as_json() for edge in edges))

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
        for item in self.nodes():
            if item.node_id == node_id:
                return item
        return None

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
        return [
            edge
            for edge in self.visible_edges(valid_at, commit_at, edge_type)
            if edge.source_id == node_id
        ]

    def incoming(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
        valid_at: datetime | None = None,
        commit_at: datetime | None = None,
    ) -> list[Edge]:
        return [
            edge
            for edge in self.visible_edges(valid_at, commit_at, edge_type)
            if edge.target_id == node_id
        ]
