"""Explainable package-name proximity plus graph-position scoring."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..config import Settings
from ..json_types import JsonObject
from ..model import EdgeType, Node, NodeType, package_id
from ..query.types import AbstentionNotice, QueryResponse
from ..store import GraphStore
from ..timeutil import format_time, now_utc, parse_time


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, 1):
        current = [left_index]
        for right_index, right_char in enumerate(right, 1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


class TyposquatScorer:
    def __init__(self, store: GraphStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        section = settings.section("inference")
        self.distance_weight = settings.number("inference", "name_distance_weight")
        self.account_age_weight = settings.number("inference", "account_age_weight")
        self.dependent_weight = settings.number("inference", "dependent_count_weight")
        self.shadow_weight = settings.number("inference", "shadow_publish_weight")
        distance_limit = section.get("max_name_distance")
        candidate_limit = section.get("candidate_limit")
        if isinstance(distance_limit, bool) or not isinstance(distance_limit, int):
            raise ValueError("inference.max_name_distance must be an integer")
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int):
            raise ValueError("inference.candidate_limit must be an integer")
        self.max_name_distance = distance_limit
        self.candidate_limit = candidate_limit
        popular_count = section.get("popular_dependent_count")
        shadow_days = section.get("shadow_window_days")
        if isinstance(popular_count, bool) or not isinstance(popular_count, int):
            raise ValueError("inference.popular_dependent_count must be an integer")
        if isinstance(shadow_days, bool) or not isinstance(shadow_days, int):
            raise ValueError("inference.shadow_window_days must be an integer")
        self.popular_dependent_count = popular_count
        self.shadow_window = timedelta(days=shadow_days)

    def score(self, registry: str, name: str, as_of: datetime | None = None) -> QueryResponse:
        instant = as_of if as_of is not None else now_utc()
        target = self.store.node(package_id(registry, name))
        if target is None:
            from ..query.types import Coverage

            coverage = Coverage(0, 0, 0)
            return QueryResponse(
                f"Typosquat proximity {registry}:{name}",
                (),
                (AbstentionNotice(name, "target Package node is missing"),),
                coverage,
            )
        target_published = self._publish_times(target.node_id)
        target_dependents = self._dependent_count(target.node_id, instant)
        results: list[JsonObject] = []
        abstentions: list[AbstentionNotice] = []
        for candidate in self.store.nodes_of_type(NodeType.PACKAGE):
            candidate_name = candidate.attributes.get("name")
            candidate_registry = candidate.attributes.get("registry")
            if not isinstance(candidate_name, str) or candidate_registry != registry or candidate_name == name:
                continue
            distance = edit_distance(name, candidate_name)
            if distance > self.max_name_distance:
                continue
            components: JsonObject = {
                "name_distance": distance,
                "name_distance_score": name_distance_score(name, candidate_name),
            }
            available_weight = self.distance_weight
            dependent_count = self._dependent_count(candidate.node_id, instant)
            dependent_score = min(dependent_count / max(1, self.popular_dependent_count), 1.0)
            components["dependent_count"] = dependent_count
            components["dependent_score"] = dependent_score
            available_weight += self.dependent_weight
            shadow_score = publish_shadow_score(target_published, self._publish_times(candidate.node_id), self.shadow_window)
            components["shadow_publish_score"] = shadow_score
            if shadow_score is not None:
                available_weight += self.shadow_weight
            else:
                abstentions.append(AbstentionNotice(candidate.node_id, "publish timestamps are insufficient for shadow-pattern scoring"))
            maintainer_age = self._account_age_score(candidate.node_id, instant)
            components["account_age_score"] = maintainer_age
            if maintainer_age is not None:
                available_weight += self.account_age_weight
            else:
                abstentions.append(AbstentionNotice(candidate.node_id, "registry record has no maintainer account creation timestamp"))
            weighted = self.distance_weight * float(components["name_distance_score"])
            weighted += self.dependent_weight * dependent_score
            if shadow_score is not None:
                weighted += self.shadow_weight * shadow_score
            if maintainer_age is not None:
                weighted += self.account_age_weight * maintainer_age
            score = weighted / available_weight if available_weight else None
            results.append(
                {
                    "candidate": candidate_name,
                    "score": score,
                    "score_denominator_weight": available_weight,
                    "components": components,
                    "explanation": "near-name candidate ranked with available graph-position evidence; unavailable fields are abstentions",
                    "as_of": format_time(instant),
                    "target_dependent_count": target_dependents,
                }
            )
        results.sort(key=lambda item: float(item["score"]) if isinstance(item.get("score"), (float, int)) else -1.0, reverse=True)
        if not results and not abstentions:
            abstentions.append(AbstentionNotice(target.node_id, "no candidate is within the configured name-distance bound"))
        return QueryResponse(
            f"Typosquat proximity {registry}:{name}",
            tuple(results[: self.candidate_limit]),
            tuple(abstentions),
            self._coverage(),
        )

    def _dependent_count(self, package_node_id: str, instant: datetime) -> int:
        return len({edge.source_id for edge in self.store.incoming(package_node_id, EdgeType.DEPENDS_ON, valid_at=instant)})

    def _publish_times(self, package_node_id: str) -> tuple[datetime, ...]:
        package = self.store.node(package_node_id)
        if package is None:
            return ()
        registry = package.attributes.get("registry")
        name = package.attributes.get("name")
        if not isinstance(registry, str) or not isinstance(name, str):
            return ()
        values: list[datetime] = []
        for node in self.store.nodes_of_type(NodeType.VERSION):
            if node.attributes.get("registry") != registry or node.attributes.get("package") != name:
                continue
            published = node.attributes.get("published_at")
            if isinstance(published, str):
                try:
                    values.append(parse_time(published, f"{node.node_id}.published_at"))
                except ValueError:
                    continue
        return tuple(sorted(values))

    def _account_age_score(self, package_node_id: str, instant: datetime) -> float | None:
        maintainer_edges = self.store.incoming(package_node_id, EdgeType.MAINTAINS, valid_at=instant)
        values: list[float] = []
        for edge in maintainer_edges:
            maintainer = self.store.node(edge.source_id)
            if maintainer is None:
                continue
            created = maintainer.attributes.get("account_created_at")
            if not isinstance(created, str):
                continue
            try:
                age_days = (instant - parse_time(created, f"{edge.source_id}.account_created_at")).days
            except ValueError:
                continue
            young_days = self.settings.integer("inference", "young_account_days")
            values.append(1.0 if age_days <= young_days else 0.0)
        return max(values) if values else None

    def _coverage(self):
        from ..query.engine import QueryEngine

        return QueryEngine(self.store, self.settings).coverage()


def name_distance_score(target: str, candidate: str) -> float:
    distance = edit_distance(target, candidate)
    denominator = max(len(target), len(candidate))
    return 1.0 - distance / denominator if denominator else 0.0


def publish_shadow_score(target: tuple[datetime, ...], candidate: tuple[datetime, ...], window: timedelta) -> float | None:
    if not target or not candidate:
        return None
    matches = 0
    for candidate_time in candidate:
        if any(abs(candidate_time - target_time) <= window for target_time in target):
            matches += 1
    return matches / len(candidate)
