"""HydraDB candidate evidence and local bitemporal verification.

HydraDB is used here for hosted graph discovery.  The records returned by
recall and graph-relation inspection are candidates, not security decisions.
Blastline only accepts a candidate after it can map the returned source IDs to
typed local edges and validate both temporal axes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

from ..errors import Abstention, ExternalCallError
from ..hydra import HydraClient
from ..json_types import JsonObject, JsonValue, require_bool, require_int, require_object, require_string
from ..model import EdgeType, TimeInterval, version_id
from ..store import GraphStore
from ..timeutil import format_time
from .engine import QueryEngine


@dataclass(frozen=True, slots=True)
class HydraEntity:
    entity_id: str | None
    name: str
    entity_type: str | None


@dataclass(frozen=True, slots=True)
class HydraRelation:
    source: HydraEntity
    predicate: str
    target: HydraEntity


@dataclass(frozen=True, slots=True)
class HydraCandidatePath:
    relations: tuple[HydraRelation, ...]
    source_chunk_ids: tuple[str, ...]
    group_id: str | None
    relevancy_score: float | None


@dataclass(frozen=True, slots=True)
class HydraWindowResult:
    target: str
    valid_at: datetime
    window_end: datetime
    known_at: datetime | None
    query: str
    candidate_paths: tuple[HydraCandidatePath, ...]
    inspected_source_ids: tuple[str, ...]
    inspected_relations: tuple[HydraRelation, ...]
    accepted_results: tuple[JsonObject, ...]
    current_repositories: tuple[str, ...]
    historical_repositories: tuple[str, ...]
    rejected_source_ids: tuple[str, ...]
    abstentions: tuple[str, ...]
    latency_ms: float
    local_hydra_agreement: bool | None
    recall_from_cache: bool
    relation_calls_from_cache: int
    retrieval_warnings: tuple[str, ...]

    def as_json(self) -> JsonObject:
        return {
            "target": self.target,
            "valid_at": format_time(self.valid_at),
            "window_end": format_time(self.window_end),
            "known_at": format_time(self.known_at) if self.known_at is not None else None,
            "query": self.query,
            "candidate_path_count": len(self.candidate_paths),
            "inspected_source_ids": list(self.inspected_source_ids),
            "inspected_relation_count": len(self.inspected_relations),
            "accepted_results": list(self.accepted_results),
            "current_repositories": list(self.current_repositories),
            "historical_repositories": list(self.historical_repositories),
            "historical_differs_current": set(self.historical_repositories) != set(self.current_repositories),
            "rejected_source_ids": list(self.rejected_source_ids),
            "abstentions": list(self.abstentions),
            "latency_ms": round(self.latency_ms, 3),
            "local_hydra_agreement": self.local_hydra_agreement,
            "recall_from_cache": self.recall_from_cache,
            "relation_calls_from_cache": self.relation_calls_from_cache,
            "retrieval_warnings": list(self.retrieval_warnings),
        }


def _optional_string(value: JsonValue | None, context: str) -> str | None:
    if value is None:
        return None
    return require_string(value, context)


def _entity(value: JsonValue, context: str) -> HydraEntity:
    body = require_object(value, context)
    entity_id = _optional_string(
        body.get("id", body.get("source_id", body.get("identifier", body.get("entity_id")))),
        f"{context}.id",
    )
    name_value = body.get("name", entity_id)
    if not isinstance(name_value, str):
        raise ValueError(f"{context}.name must be a string")
    entity_type = _optional_string(body.get("type"), f"{context}.type")
    return HydraEntity(entity_id, name_value, entity_type)


def _relation(value: JsonValue, context: str) -> HydraRelation:
    body = require_object(value, context)
    predicate_value = body.get("relation")
    predicate_body = require_object(predicate_value, f"{context}.relation")
    predicate = predicate_body.get("canonical_predicate", predicate_body.get("name"))
    if not isinstance(predicate, str) or not predicate:
        raise ValueError(f"{context}.relation.canonical_predicate must be a non-empty string")
    return HydraRelation(
        source=_entity(body.get("source"), f"{context}.source"),
        predicate=predicate,
        target=_entity(body.get("target"), f"{context}.target"),
    )


def _string_tuple(value: JsonValue | None, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(require_string(item, f"{context}[{index}]"))
    return tuple(result)


def parse_candidate_paths(body: JsonObject) -> tuple[HydraCandidatePath, ...]:
    """Parse only the documented structured graph-context path fields."""

    graph_context = require_object(body.get("graph_context"), "Hydra response.graph_context")
    raw_paths = graph_context.get("query_paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raw_paths = graph_context.get("chunk_relations")
    if not isinstance(raw_paths, list):
        raise ValueError("Hydra response.graph_context.query_paths or chunk_relations must be an array")
    paths: list[HydraCandidatePath] = []
    for index, raw_path in enumerate(raw_paths):
        path = require_object(raw_path, f"Hydra query_paths[{index}]")
        raw_relations = path.get("triplets")
        if not isinstance(raw_relations, list):
            raise ValueError(f"Hydra query_paths[{index}].triplets must be an array")
        relations = tuple(_relation(item, f"Hydra query_paths[{index}].triplets[{item_index}]") for item_index, item in enumerate(raw_relations))
        score_value = path.get("relevancy_score")
        if score_value is not None and (isinstance(score_value, bool) or not isinstance(score_value, (int, float))):
            raise ValueError(f"Hydra query_paths[{index}].relevancy_score must be numeric")
        group_id = _optional_string(path.get("group_id"), f"Hydra query_paths[{index}].group_id")
        paths.append(
            HydraCandidatePath(
                relations=relations,
                source_chunk_ids=_string_tuple(path.get("source_chunk_ids"), f"Hydra query_paths[{index}].source_chunk_ids"),
                group_id=group_id,
                relevancy_score=float(score_value) if score_value is not None else None,
            )
        )
    return tuple(paths)


def parse_relations(body: JsonObject) -> tuple[HydraRelation, ...]:
    raw_relations = body.get("relations")
    if not isinstance(raw_relations, list):
        raise ValueError("Hydra graph relation response.relations must be an array")
    result: list[HydraRelation] = []
    for index, raw_relation in enumerate(raw_relations):
        item = require_object(raw_relation, f"Hydra relations[{index}]")
        nested = item.get("relations")
        if isinstance(nested, list):
            source = item.get("source")
            target = item.get("target")
            for relation_index, raw_edge in enumerate(nested):
                edge = require_object(raw_edge, f"Hydra relations[{index}].relations[{relation_index}]")
                result.append(
                    _relation(
                        {"source": source, "target": target, "relation": edge},
                        f"Hydra relations[{index}].relations[{relation_index}]",
                    )
                )
        else:
            result.append(_relation(item, f"Hydra relations[{index}]"))
    return tuple(result)


def response_source_ids(body: JsonObject) -> tuple[str, ...]:
    raw_chunks = body.get("chunks")
    if not isinstance(raw_chunks, list):
        raise ValueError("Hydra response.chunks must be an array")
    source_ids: list[str] = []
    for index, raw_chunk in enumerate(raw_chunks):
        chunk = require_object(raw_chunk, f"Hydra chunks[{index}]")
        source_id = chunk.get("source_id", chunk.get("id"))
        if isinstance(source_id, str) and source_id not in source_ids:
            source_ids.append(source_id)
    return tuple(source_ids)


def parse_typed_edge_metadata(body: JsonObject) -> dict[str, JsonObject]:
    """Require typed identity on returned Blastline chunks.

    The function name is retained because temporal edge metadata is the
    security-critical branch. Node chunks are validated too, so a graph path
    containing Repository or Resolution records is not rejected merely for
    being a node record.
    """

    raw_chunks = body.get("chunks")
    if not isinstance(raw_chunks, list):
        raise ValueError("Hydra response.chunks must be an array")
    result: dict[str, JsonObject] = {}
    for index, raw_chunk in enumerate(raw_chunks):
        chunk = require_object(raw_chunk, f"Hydra chunks[{index}]")
        source_id = require_string(chunk.get("source_id", chunk.get("id")), f"Hydra chunks[{index}].source_id")
        metadata_value = chunk.get("additional_metadata", chunk.get("metadata"))
        metadata = require_object(metadata_value, f"Hydra chunks[{index}].additional_metadata")
        nested_evidence = metadata.get("blastline_evidence")
        if isinstance(nested_evidence, dict):
            metadata = nested_evidence
        record_type = require_string(metadata.get("blastline_record_type"), f"Hydra chunks[{index}].additional_metadata.blastline_record_type")
        if record_type == "edge":
            aliases = {
                "edge_id": ("edge_id", "blastline_edge_id"),
                "source_id": ("source_id", "blastline_source_id"),
                "target_id": ("target_id", "blastline_target_id"),
                "valid_start": ("valid_start", "blastline_valid_start"),
                "commit_at": ("commit_at", "blastline_commit_at"),
                "graph_fingerprint": ("graph_fingerprint", "blastline_graph_fingerprint"),
            }
            normalized: JsonObject = {"blastline_record_type": record_type}
            for field, candidates in aliases.items():
                value = next((metadata.get(candidate) for candidate in candidates if metadata.get(candidate) is not None), None)
                normalized[field] = require_string(value, f"Hydra chunks[{index}].additional_metadata.{field}")
            valid_end = next((metadata.get(candidate) for candidate in ("valid_end", "blastline_valid_end") if metadata.get(candidate) is not None), None)
            if valid_end is not None:
                normalized["valid_end"] = require_string(valid_end, f"Hydra chunks[{index}].additional_metadata.valid_end")
            result[source_id] = normalized
            continue
        elif record_type == "node":
            normalized = {
                "blastline_record_type": record_type,
                "blastline_node_id": require_string(metadata.get("blastline_node_id"), f"Hydra chunks[{index}].additional_metadata.blastline_node_id"),
                "blastline_node_type": require_string(metadata.get("blastline_node_type"), f"Hydra chunks[{index}].additional_metadata.blastline_node_type"),
                "graph_fingerprint": require_string(
                    metadata.get("graph_fingerprint", metadata.get("blastline_graph_fingerprint")),
                    f"Hydra chunks[{index}].additional_metadata.graph_fingerprint",
                ),
            }
        else:
            raise ValueError(f"Hydra chunks[{index}].additional_metadata.blastline_record_type is unsupported: {record_type}")
        result[source_id] = normalized
    return result


def parse_listed_typed_metadata(body: JsonObject) -> tuple[dict[str, JsonObject], bool]:
    """Parse typed Blastline metadata from one paginated collection page."""

    raw_sources = body.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("Hydra context list response.sources must be an array")
    chunks: list[JsonObject] = []
    for index, raw_source in enumerate(raw_sources):
        source = require_object(raw_source, f"Hydra sources[{index}]")
        source_id = require_string(source.get("id"), f"Hydra sources[{index}].id")
        metadata = require_object(source.get("additional_metadata"), f"Hydra sources[{index}].additional_metadata")
        evidence = metadata.get("blastline_evidence")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("blastline_record_type"), str):
            continue
        chunks.append({"source_id": source_id, "additional_metadata": metadata})
    result = parse_typed_edge_metadata({"chunks": chunks})
    pagination = require_object(body.get("pagination"), "Hydra context list response.pagination")
    require_int(pagination.get("page"), "Hydra context list response.pagination.page")
    has_next = require_bool(pagination.get("has_next"), "Hydra context list response.pagination.has_next")
    return result, has_next


def local_id(source_id: str) -> str:
    return source_id.removeprefix("blastline:")


def source_id_from_chunk_id(chunk_id: str) -> str:
    marker = "_chunk_"
    return chunk_id.split(marker, 1)[0] if marker in chunk_id else chunk_id


def hydra_query(
    registry: str,
    package: str,
    version: str,
    window: tuple[datetime, datetime],
    known_at: datetime | None,
) -> str:
    valid_at, window_end = window
    known_text = format_time(known_at) if known_at is not None else "latest-known"
    return (
        f"Blastline supply-chain exposure graph path for {registry}/{package}@{version}; "
        f"valid_window=[{format_time(valid_at)},{format_time(window_end)}); "
        f"valid_at={format_time(valid_at)}; known_at={known_text}; "
        "required path Repository DECLARES Resolution RESOLVED_TO Version"
    )


class HydraWindowVerifier:
    def __init__(self, hydra: HydraClient, store: GraphStore, engine: QueryEngine, max_results: int = 50) -> None:
        if max_results < 1:
            raise ValueError("Hydra candidate result limit must be positive")
        self.hydra = hydra
        self.store = store
        self.engine = engine
        self.max_results = max_results
        self._listed_metadata: dict[str, JsonObject] | None = None

    def run(
        self,
        registry: str,
        package: str,
        version: str,
        window: tuple[datetime, datetime],
        known_at: datetime | None,
    ) -> HydraWindowResult:
        if not self.hydra.live_enabled:
            raise Abstention("HydraDB candidate query requires HYDRA_DB_API_KEY")
        start_clock = perf_counter()
        valid_at = window[0]
        query = hydra_query(registry, package, version, window, known_at)
        target_id = version_id(registry, package, version)
        retrieval_warnings: list[str] = []
        recall_from_cache = False
        try:
            recall_response = self.hydra.recall(query, self.max_results, cache=False)
            recall_from_cache = recall_response.from_cache
            try:
                paths = parse_candidate_paths(recall_response.body)
                recalled_source_ids = response_source_ids(recall_response.body)
                typed_metadata = parse_typed_edge_metadata(recall_response.body)
            except ValueError as exc:
                raise Abstention(f"HydraDB candidate response is not typed Blastline evidence: {exc}") from exc
            if not paths:
                retrieval_warnings.append("HydraDB graph-context recall returned no structured query paths")
        except ExternalCallError as exc:
            paths = ()
            recalled_source_ids = ()
            typed_metadata = {}
            retrieval_warnings.append(f"HydraDB graph-context recall unavailable; used exhaustive hosted collection retrieval: {exc}")
        missing_metadata = [source_id for source_id in recalled_source_ids if source_id.startswith("blastline:") and source_id not in typed_metadata]
        if missing_metadata:
            raise Abstention("HydraDB candidate edge metadata is incomplete for source IDs: " + ", ".join(sorted(missing_metadata)))

        path_source_ids = {
            source_id_from_chunk_id(chunk_id)
            for path in paths
            for chunk_id in path.source_chunk_ids
        }
        recalled_candidate_ids = tuple(
            source_id
            for source_id in recalled_source_ids
            if not path_source_ids or source_id in path_source_ids
        )

        # Ranked recall is intentionally non-exhaustive. Enumerate the hosted
        # collection through its documented pagination surface so an incident
        # query does not mistake a relevance cutoff for a complete answer.
        if self._listed_metadata is None:
            listed_metadata: dict[str, JsonObject] = {}
            page = 1
            while True:
                try:
                    page_metadata, has_next = parse_listed_typed_metadata(self.hydra.list_sources(page, 100).body)
                except ValueError as exc:
                    raise Abstention(f"HydraDB collection listing is not typed Blastline evidence: {exc}") from exc
                listed_metadata.update(page_metadata)
                if not has_next:
                    break
                page += 1
            self._listed_metadata = listed_metadata
        listed_metadata = self._listed_metadata
        exhaustive_candidate_ids = tuple(
            source_id
            for source_id, metadata in listed_metadata.items()
            if metadata.get("blastline_record_type") == "edge" and metadata.get("target_id") == target_id
        )
        source_ids = tuple(dict.fromkeys((*recalled_candidate_ids, *exhaustive_candidate_ids)))
        typed_metadata.update(listed_metadata)

        relations = [relation for path in paths for relation in path.relations]
        relation_cache_hits = 0
        evidence_ids = {local_id(item) for item in source_ids}
        for path in paths:
            for relation in path.relations:
                if relation.source.entity_id is not None:
                    evidence_ids.add(local_id(relation.source.entity_id))
                if relation.target.entity_id is not None:
                    evidence_ids.add(local_id(relation.target.entity_id))

        evidence_edges = [edge for edge in self.store.edges() if edge.edge_id in evidence_ids]
        target_edges = [
            edge
            for edge in evidence_edges
            if edge.edge_type is EdgeType.RESOLVED_TO
            and edge.target_id == target_id
            and edge.valid.intersects(TimeInterval(window[0], window[1]))
            and (known_at is None or edge.commit_at <= known_at)
        ]
        verified_repository_ids: set[str] = set()
        for edge in target_edges:
            verified_repository_ids.update(
                declared.source_id for declared in self.store.incoming(edge.source_id, EdgeType.DECLARES) if known_at is None or declared.commit_at <= known_at
            )

        local_response = self.engine.window_exposure(registry, package, version, window, known_at)
        current_response = self.engine.current_exposure(registry, package, version)
        local_by_repository = {
            item["repository"]: item
            for item in local_response.results
            if isinstance(item.get("repository"), str)
        }
        accepted = tuple(
            item for repository, item in local_by_repository.items()
            if any(self._repository_label(repo_id) == repository for repo_id in verified_repository_ids)
        )
        accepted_repositories = {item.get("repository") for item in accepted}
        local_repositories = set(local_by_repository)
        current_repositories = tuple(
            sorted(
                str(item["repository"])
                for item in current_response.results
                if isinstance(item.get("repository"), str)
            )
        )
        target_edge_ids = {edge.edge_id for edge in target_edges}
        mapped_edge_ids = {edge.edge_id for edge in evidence_edges}
        rejected = tuple(sorted(local_id(source_id) for source_id in source_ids if local_id(source_id) in mapped_edge_ids and local_id(source_id) not in target_edge_ids))
        abstentions: list[str] = []
        if not evidence_edges:
            abstentions.append("HydraDB returned paths but no source ID mapped to a local typed edge")
        if not target_edges:
            abstentions.append("HydraDB evidence did not contain a verifiable RESOLVED_TO edge for the requested version and window")
        if local_response.abstentions:
            abstentions.extend(f"local temporal verifier: {notice.reason}" for notice in local_response.abstentions)
        if current_response.abstentions:
            abstentions.extend(f"current-state comparison: {notice.reason}" for notice in current_response.abstentions)
        agreement: bool | None = None if abstentions else accepted_repositories == local_repositories
        return HydraWindowResult(
            target=f"{registry}:{package}@{version}",
            valid_at=valid_at,
            window_end=window[1],
            known_at=known_at,
            query=query,
            candidate_paths=paths,
            inspected_source_ids=source_ids,
            inspected_relations=tuple(relations),
            accepted_results=accepted,
            current_repositories=current_repositories,
            historical_repositories=tuple(sorted(str(item) for item in local_repositories)),
            rejected_source_ids=rejected,
            abstentions=tuple(dict.fromkeys(abstentions)),
            latency_ms=(perf_counter() - start_clock) * 1000.0,
            local_hydra_agreement=agreement,
            recall_from_cache=recall_from_cache,
            relation_calls_from_cache=relation_cache_hits,
            retrieval_warnings=tuple(retrieval_warnings),
        )

    def _repository_label(self, node_id: str) -> str:
        node = self.store.node(node_id)
        if node is None:
            return node_id
        value = node.attributes.get("full_name")
        return value if isinstance(value, str) else node_id
