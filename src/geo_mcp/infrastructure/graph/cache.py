from __future__ import annotations

import json
import logging
from pathlib import Path

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.infrastructure.config import Settings

logger = logging.getLogger(__name__)

CACHE_VERSION = 2


class DiskGraphCache:
    def __init__(self, settings: Settings) -> None:
        self._dir = settings.cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, bbox: BoundingBox) -> Path:
        return self._dir / f"v{CACHE_VERSION}_{bbox.cache_key()}.json"

    def load(self, bbox: BoundingBox) -> NetworkGraph | None:
        path = self.path_for(bbox.rounded())
        if not path.exists():
            # Also try unrounded key for exact matches written earlier.
            path = self.path_for(bbox)
            if not path.exists():
                return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return deserialize_graph(data)
        except Exception:
            logger.exception("Failed to load graph cache from %s", path)
            return None

    def save(self, bbox: BoundingBox, graph: NetworkGraph) -> None:
        key_bbox = bbox.rounded()
        path = self.path_for(key_bbox)
        path.write_text(
            json.dumps(serialize_graph(graph), ensure_ascii=True),
            encoding="utf-8",
        )


def serialize_graph(graph: NetworkGraph) -> dict:
    return {
        "version": CACHE_VERSION,
        "bbox": (
            None
            if graph.bbox is None
            else {
                "south": graph.bbox.south,
                "west": graph.bbox.west,
                "north": graph.bbox.north,
                "east": graph.bbox.east,
            }
        ),
        "major_roads": graph.major_roads,
        "landmarks": graph.landmarks,
        "nodes": [
            {
                "id": n.id,
                "lat": n.point.lat,
                "lon": n.point.lon,
                "kind": n.kind,
                "name": n.name,
                "tags": n.tags,
            }
            for n in graph.nodes.values()
        ],
        "edges": [
            {
                "source_id": e.source_id,
                "target_id": e.target_id,
                "mode": e.mode.value,
                "length_m": e.length_m,
                "travel_time_s": e.travel_time_s,
                "name": e.name,
                "line_ref": e.line_ref,
                "tags": e.tags,
            }
            for edges in graph.adjacency.values()
            for e in edges
        ],
    }


def deserialize_graph(data: dict) -> NetworkGraph:
    graph = NetworkGraph(
        major_roads=list(data.get("major_roads") or []),
        landmarks=list(data.get("landmarks") or []),
    )
    bbox = data.get("bbox")
    if bbox:
        graph.bbox = BoundingBox(**bbox)
    for n in data.get("nodes") or []:
        graph.add_node(
            GraphNode(
                id=n["id"],
                point=GeoPoint(lat=n["lat"], lon=n["lon"]),
                kind=n.get("kind", "intersection"),
                name=n.get("name"),
                tags=n.get("tags") or {},
            )
        )
    for e in data.get("edges") or []:
        graph.add_edge(
            GraphEdge(
                source_id=e["source_id"],
                target_id=e["target_id"],
                mode=EdgeMode(e["mode"]),
                length_m=float(e["length_m"]),
                travel_time_s=float(e["travel_time_s"]),
                name=e.get("name"),
                line_ref=e.get("line_ref"),
                tags=e.get("tags") or {},
            )
        )
    return graph
