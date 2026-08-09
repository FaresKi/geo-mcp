from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import inf

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint


class EdgeMode(StrEnum):
    WALK = "walk"
    TRANSIT = "transit"
    TRANSFER = "transfer"


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    point: GeoPoint
    kind: str = "intersection"  # intersection | stop | poi
    name: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source_id: str
    target_id: str
    mode: EdgeMode
    length_m: float
    travel_time_s: float
    name: str | None = None
    line_ref: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class NetworkGraph:
    """Directed multimodal network stored as adjacency lists."""

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    adjacency: dict[str, list[GraphEdge]] = field(default_factory=dict)
    bbox: BoundingBox | None = None
    major_roads: list[str] = field(default_factory=list)
    landmarks: list[str] = field(default_factory=list)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node
        self.adjacency.setdefault(node.id, [])

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.source_id not in self.nodes or edge.target_id not in self.nodes:
            raise ValueError("Edge endpoints must exist before adding edge")
        self.adjacency.setdefault(edge.source_id, []).append(edge)

    def neighbors(self, node_id: str) -> list[GraphEdge]:
        return self.adjacency.get(node_id, [])

    def nearest_node(
        self,
        point: GeoPoint,
        *,
        kind: str | None = None,
        modes_from: set[EdgeMode] | None = None,
    ) -> tuple[GraphNode, float] | None:
        best: GraphNode | None = None
        best_dist = inf
        for node in self.nodes.values():
            if kind is not None and node.kind != kind:
                continue
            if modes_from is not None:
                edges = self.adjacency.get(node.id, [])
                if not any(e.mode in modes_from for e in edges) and node.kind != "stop":
                    # Prefer nodes that participate in walk/transit connectivity.
                    if node.kind != "intersection":
                        continue
            dist = point.distance_meters(node.point)
            if dist < best_dist:
                best_dist = dist
                best = node
        if best is None:
            return None
        return best, best_dist

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self.adjacency.values())

    def stop_nodes(self) -> list[GraphNode]:
        return [n for n in self.nodes.values() if n.kind == "stop"]
