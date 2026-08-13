"""Turn normalized registry records into canonical graph records."""

from __future__ import annotations

from collections.abc import Iterable

from ..model import Edge, EdgeType, Node, NodeType, TimeInterval, package_id, version_id
from .records import RegistryPackage, RegistryVersion


def graphify_package(package: RegistryPackage) -> tuple[list[Node], list[Edge]]:
    nodes: list[Node] = [
        Node(
            package_id(package.registry, package.name),
            NodeType.PACKAGE,
            {"registry": package.registry, "name": package.name},
        ),
        Node(
            f"publish-infra:{package.registry}",
            NodeType.PUBLISH_INFRA,
            {"registry": package.registry},
        ),
    ]
    edges: list[Edge] = []
    package_node_id = package_id(package.registry, package.name)
    infra_node_id = f"publish-infra:{package.registry}"
    for version in package.versions:
        nodes.append(
            Node(
                version_id(package.registry, package.name, version.version),
                NodeType.VERSION,
                {
                    "registry": version.registry,
                    "package": version.package_name,
                    "version": version.version,
                    "published_at": version.published_at.isoformat(),
                    "yanked": version.yanked,
                },
            )
        )
        version_node_id = version_id(package.registry, package.name, version.version)
        interval = TimeInterval(version.published_at)
        edges.append(
            Edge.create(
                version_node_id,
                EdgeType.PUBLISHED_FROM,
                infra_node_id,
                interval,
                version.modified_at,
                {"source": version.source_identifier},
            )
        )
        for dependency in version.dependencies:
            dependency_node_id = package_id(package.registry, dependency.name)
            nodes.append(
                Node(
                    dependency_node_id,
                    NodeType.PACKAGE,
                    {"registry": package.registry, "name": dependency.name},
                )
            )
            edges.append(
                Edge.create(
                    version_node_id,
                    EdgeType.DEPENDS_ON,
                    dependency_node_id,
                    interval,
                    version.modified_at,
                    {"requirement": dependency.requirement, "source": version.source_identifier},
                )
            )
        for maintainer_name in version.maintainers:
            maintainer_node_id = f"maintainer:{package.registry}:{maintainer_name}"
            nodes.append(Node(maintainer_node_id, NodeType.MAINTAINER, {"name": maintainer_name, "registry": package.registry}))
            edges.append(
                Edge.create(
                    version_node_id,
                    EdgeType.PUBLISHED_BY,
                    maintainer_node_id,
                    interval,
                    version.modified_at,
                    {"source": version.source_identifier},
                )
            )
            edges.append(
                Edge.create(
                    maintainer_node_id,
                    EdgeType.MAINTAINS,
                    package_node_id,
                    interval,
                    version.modified_at,
                    {"source": version.source_identifier},
                )
            )
    return deduplicate_nodes(nodes), deduplicate_edges(edges)


def deduplicate_nodes(nodes: Iterable[Node]) -> list[Node]:
    by_id: dict[str, Node] = {}
    for node in nodes:
        existing = by_id.get(node.node_id)
        if existing is not None and existing != node:
            raise ValueError(f"node identity collision with different attributes: {node.node_id}")
        by_id[node.node_id] = node
    return [by_id[node_id] for node_id in sorted(by_id)]


def deduplicate_edges(edges: Iterable[Edge]) -> list[Edge]:
    by_id: dict[str, Edge] = {}
    for edge in edges:
        by_id[edge.edge_id] = edge
    return [by_id[edge_id] for edge_id in sorted(by_id)]
