from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import inf
from typing import TYPE_CHECKING

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint

if TYPE_CHECKING:
    from geo_mcp.domain.services.spatial_index import SpatialIndex


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
    _index: SpatialIndex | None = field(default=None, repr=False, compare=False)

    def ensure_index(self) -> SpatialIndex:
        if self._index is None:
            from geo_mcp.domain.services.spatial_index import SpatialIndex as _SpatialIndex

            self._index = _SpatialIndex()
            self._index.rebuild(self.nodes)
        return self._index

    def invalidate_index(self) -> None:
        self._index = None

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node
        self.adjacency.setdefault(node.id, [])
        if self._index is not None:
            self._index.add(node)

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
        max_radius_m: float | None = None,
    ) -> tuple[GraphNode, float] | None:
        index = self.ensure_index()
        hit = index.nearest(
            point,
            kind=kind,
            modes_from=modes_from,
            graph=self,
            max_radius_m=max_radius_m,
        )
        if hit is not None:
            return hit
        best: GraphNode | None = None
        best_dist = inf
        for node in self.nodes.values():
            if kind is not None and node.kind != kind:
                continue
            if modes_from is not None:
                edges = self.adjacency.get(node.id, [])
                if not any(e.mode in modes_from for e in edges) and node.kind != "stop":
                    if node.kind != "intersection":
                        continue
            dist = point.distance_meters(node.point)
            if dist < best_dist:
                best_dist = dist
                best = node
        if best is None:
            return None
        if max_radius_m is not None and best_dist > max_radius_m:
            return None
        return best, best_dist

    def nearest_stops(
        self,
        point: GeoPoint,
        *,
        limit: int = 8,
        max_radius_m: float | None = None,
    ) -> list[tuple[GraphNode, float]]:
        return self.ensure_index().nearest_many(
            point, kind="stop", limit=limit, max_radius_m=max_radius_m
        )

    def merge_from(self, other: NetworkGraph) -> None:
        """Merge another graph's nodes/edges into this one (idempotent by id)."""
        for node in other.nodes.values():
            if node.id not in self.nodes:
                self.add_node(node)
        for src, edges in other.adjacency.items():
            existing = {
                (e.target_id, e.mode, e.line_ref, round(e.length_m, 2))
                for e in self.adjacency.get(src, [])
            }
            for edge in edges:
                key = (edge.target_id, edge.mode, edge.line_ref, round(edge.length_m, 2))
                if key in existing:
                    continue
                if edge.target_id not in self.nodes:
                    continue
                self.add_edge(edge)
                existing.add(key)
        if other.bbox is not None:
            if self.bbox is None:
                self.bbox = other.bbox
            else:
                self.bbox = BoundingBox(
                    south=min(self.bbox.south, other.bbox.south),
                    west=min(self.bbox.west, other.bbox.west),
                    north=max(self.bbox.north, other.bbox.north),
                    east=max(self.bbox.east, other.bbox.east),
                )
        for road in other.major_roads:
            if road not in self.major_roads:
                self.major_roads.append(road)
        for landmark in other.landmarks:
            if landmark not in self.landmarks:
                self.landmarks.append(landmark)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self.adjacency.values())

    def stop_nodes(self) -> list[GraphNode]:
        return [n for n in self.nodes.values() if n.kind == "stop"]
