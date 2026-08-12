from __future__ import annotations

from collections import defaultdict
from math import floor, inf

from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphNode, NetworkGraph


class SpatialIndex:
    """Grid-bucket nearest-neighbor index over graph nodes."""

    def __init__(self, cell_deg: float = 0.002) -> None:
        self.cell_deg = cell_deg
        self._buckets: dict[tuple[int, int], list[GraphNode]] = defaultdict(list)
        self._nodes: dict[str, GraphNode] = {}

    def clear(self) -> None:
        self._buckets.clear()
        self._nodes.clear()

    def rebuild(self, nodes: dict[str, GraphNode]) -> None:
        self.clear()
        for node in nodes.values():
            self.add(node)

    def add(self, node: GraphNode) -> None:
        self._nodes[node.id] = node
        self._buckets[self._key(node.point)].append(node)

    def _key(self, point: GeoPoint) -> tuple[int, int]:
        return (
            floor(point.lat / self.cell_deg),
            floor(point.lon / self.cell_deg),
        )

    def nearest(
        self,
        point: GeoPoint,
        *,
        kind: str | None = None,
        modes_from: set[EdgeMode] | None = None,
        graph: NetworkGraph | None = None,
        max_radius_m: float | None = None,
        max_ring: int = 8,
    ) -> tuple[GraphNode, float] | None:
        if not self._nodes:
            return None

        origin = self._key(point)
        best: GraphNode | None = None
        best_dist = inf

        for ring in range(max_ring + 1):
            candidates: list[GraphNode] = []
            for dlat in range(-ring, ring + 1):
                for dlon in range(-ring, ring + 1):
                    # Only outer ring cells when ring > 0
                    if ring > 0 and abs(dlat) != ring and abs(dlon) != ring:
                        continue
                    candidates.extend(
                        self._buckets.get((origin[0] + dlat, origin[1] + dlon), [])
                    )
            for node in candidates:
                if kind is not None and node.kind != kind:
                    continue
                if modes_from is not None and graph is not None:
                    edges = graph.adjacency.get(node.id, [])
                    if (
                        not any(e.mode in modes_from for e in edges)
                        and node.kind != "stop"
                        and node.kind != "intersection"
                    ):
                        continue
                dist = point.distance_meters(node.point)
                if dist < best_dist:
                    best_dist = dist
                    best = node
            # Once we have a hit inside the covered radius of this ring, stop expanding.
            if best is not None:
                # Approximate ring coverage: cell diagonal ~ cell_deg * 111km * sqrt(2)
                covered_m = (ring + 0.75) * self.cell_deg * 111_000.0
                if best_dist <= covered_m:
                    break

        if best is None:
            return None
        if max_radius_m is not None and best_dist > max_radius_m:
            return None
        return best, best_dist

    def nearest_many(
        self,
        point: GeoPoint,
        *,
        kind: str | None = None,
        limit: int = 8,
        max_radius_m: float | None = None,
    ) -> list[tuple[GraphNode, float]]:
        ranked: list[tuple[float, GraphNode]] = []
        for node in self._nodes.values():
            if kind is not None and node.kind != kind:
                continue
            dist = point.distance_meters(node.point)
            if max_radius_m is not None and dist > max_radius_m:
                continue
            ranked.append((dist, node))
        ranked.sort(key=lambda item: item[0])
        return [(node, dist) for dist, node in ranked[:limit]]
