from __future__ import annotations

import logging
from collections import defaultdict

import osmnx as ox

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.domain.model.transit import TransitLine, TransitStop
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.graph.cache import DiskGraphCache
from geo_mcp.infrastructure.osm.overpass import OverpassClient

logger = logging.getLogger(__name__)


class OsmNetworkRepository:
    """Builds a multimodal walk+transit NetworkGraph from OSM and caches it."""

    def __init__(
        self,
        settings: Settings,
        overpass: OverpassClient,
        cache: DiskGraphCache | None = None,
    ) -> None:
        self._settings = settings
        self._overpass = overpass
        self._cache = cache or DiskGraphCache(settings)
        self._memory: dict[str, NetworkGraph] = {}
        # Latest loaded graph + transit catalog for use cases that need stops/lines.
        self.current_graph: NetworkGraph | None = None
        self.current_stops: list[TransitStop] = []
        self.current_lines: list[TransitLine] = []

    def get_cached(self, bbox: BoundingBox) -> NetworkGraph | None:
        key = bbox.rounded().cache_key()
        if key in self._memory:
            return self._memory[key]
        graph = self._cache.load(bbox)
        if graph is not None:
            self._memory[key] = graph
            self.current_graph = graph
        return graph

    def load_area(
        self,
        bbox: BoundingBox,
        *,
        force_refresh: bool = False,
    ) -> NetworkGraph:
        key = bbox.rounded().cache_key()
        if not force_refresh:
            cached = self.get_cached(bbox)
            if cached is not None:
                # Refresh in-memory transit catalog from graph nodes if needed.
                self.current_stops = self._stops_from_graph(cached)
                self.current_lines = []
                return cached

        logger.info("Fetching walk network for bbox %s", key)
        walk = self._load_walk_graph(bbox)

        stops: list[TransitStop] = []
        lines: list[TransitLine] = []
        try:
            stops, lines = self._overpass.fetch_transit(bbox)
            self._attach_transit(walk, stops, lines)
        except Exception:
            logger.exception(
                "Transit overlay failed for %s; continuing with walk-only graph",
                key,
            )

        try:
            features = self._overpass.describe_features(bbox)
            walk.major_roads = features.get("major_roads", [])
            walk.landmarks = features.get("landmarks", [])
        except Exception:
            logger.exception("Area feature enrichment failed for %s", key)

        walk.bbox = bbox.rounded()

        self._cache.save(bbox, walk)
        self._memory[key] = walk
        self.current_graph = walk
        self.current_stops = stops
        self.current_lines = lines
        return walk

    def list_stops_nearby(
        self,
        point: GeoPoint,
        *,
        radius_m: float = 500.0,
        limit: int = 20,
    ) -> list[TransitStop]:
        stops = self.current_stops
        if not stops and self.current_graph is not None:
            stops = self._stops_from_graph(self.current_graph)
        ranked = []
        for stop in stops:
            dist = point.distance_meters(stop.point)
            if dist <= radius_m:
                ranked.append((dist, stop))
        ranked.sort(key=lambda item: item[0])
        return [s for _, s in ranked[:limit]]

    def list_lines(self, bbox: BoundingBox) -> list[TransitLine]:
        if self.current_lines:
            return self.current_lines
        # Lazy fetch when only cached walk graph exists.
        stops, lines = self._overpass.fetch_transit(bbox)
        self.current_stops = stops
        self.current_lines = lines
        return lines

    def _load_walk_graph(self, bbox: BoundingBox) -> NetworkGraph:
        # OSMnx 2.x: bbox is (left, bottom, right, top) = (west, south, east, north)
        ox_bbox = (bbox.west, bbox.south, bbox.east, bbox.north)
        g = ox.graph_from_bbox(ox_bbox, network_type="walk", simplify=True)
        graph = NetworkGraph(bbox=bbox.rounded())

        for node_id, data in g.nodes(data=True):
            graph.add_node(
                GraphNode(
                    id=f"n:{node_id}",
                    point=GeoPoint(lat=float(data["y"]), lon=float(data["x"])),
                    kind="intersection",
                    name=data.get("name"),
                    tags={
                        k: str(v)
                        for k, v in data.items()
                        if k not in {"y", "x"} and isinstance(v, (str, int, float))
                    },
                )
            )

        walk_speed = self._settings.walk_speed_mps
        for u, v, data in g.edges(data=True):
            length = float(data.get("length") or 0.0)
            if length <= 0:
                continue
            name = data.get("name")
            if isinstance(name, list):
                name = name[0] if name else None
            travel = length / walk_speed
            graph.add_edge(
                GraphEdge(
                    source_id=f"n:{u}",
                    target_id=f"n:{v}",
                    mode=EdgeMode.WALK,
                    length_m=length,
                    travel_time_s=travel,
                    name=str(name) if name else None,
                    tags={
                        "highway": str(data["highway"])
                        if "highway" in data
                        else "footway"
                    },
                )
            )
        return graph

    def _attach_transit(
        self,
        graph: NetworkGraph,
        stops: list[TransitStop],
        lines: list[TransitLine],
    ) -> None:
        for stop in stops:
            graph.add_node(
                GraphNode(
                    id=stop.id,
                    point=stop.point,
                    kind="stop",
                    name=stop.name,
                    tags={
                        "modes": ",".join(stop.modes),
                        "lines": ",".join(stop.lines),
                    },
                )
            )
            nearest = graph.nearest_node(stop.point, kind="intersection")
            if nearest is None:
                continue
            walk_node, dist = nearest
            if dist > self._settings.max_transfer_snap_m:
                continue
            travel = dist / self._settings.walk_speed_mps
            # Bidirectional transfer links between stop and walk network.
            graph.add_edge(
                GraphEdge(
                    source_id=stop.id,
                    target_id=walk_node.id,
                    mode=EdgeMode.TRANSFER,
                    length_m=dist,
                    travel_time_s=travel,
                    name="transfer",
                )
            )
            graph.add_edge(
                GraphEdge(
                    source_id=walk_node.id,
                    target_id=stop.id,
                    mode=EdgeMode.TRANSFER,
                    length_m=dist,
                    travel_time_s=travel,
                    name="transfer",
                )
            )

        # Ride edges along consecutive mapped stops on each route relation.
        for line in lines:
            stop_ids = list(dict.fromkeys(sid for sid in line.stop_ids if sid in graph.nodes))
            pairs: list[tuple[str, str]]
            if line.mode == "ferry" and len(stop_ids) >= 2:
                # Ferry relations often interleave platforms/stop_positions;
                # connect every unique stop pair so crossings stay reachable.
                pairs = [
                    (a, b)
                    for i, a in enumerate(stop_ids)
                    for b in stop_ids[i + 1 :]
                ]
            else:
                pairs = list(zip(stop_ids, stop_ids[1:], strict=False))

            for a, b in pairs:
                pa = graph.nodes[a].point
                pb = graph.nodes[b].point
                length = pa.distance_meters(pb)
                if length <= 0:
                    continue
                speed = (
                    self._settings.ferry_speed_mps
                    if line.mode == "ferry"
                    else self._settings.transit_speed_mps
                )
                travel = length / speed
                for src, tgt in ((a, b), (b, a)):
                    graph.add_edge(
                        GraphEdge(
                            source_id=src,
                            target_id=tgt,
                            mode=EdgeMode.TRANSIT,
                            length_m=length,
                            travel_time_s=travel,
                            name=line.name,
                            line_ref=line.ref,
                            tags={"route": line.mode},
                        )
                    )

    def _stops_from_graph(self, graph: NetworkGraph) -> list[TransitStop]:
        stops: list[TransitStop] = []
        for node in graph.stop_nodes():
            modes = tuple(
                m for m in (node.tags.get("modes") or "transit").split(",") if m
            )
            lines = tuple(m for m in (node.tags.get("lines") or "").split(",") if m)
            stops.append(
                TransitStop(
                    id=node.id,
                    name=node.name or node.id,
                    point=node.point,
                    modes=modes or ("transit",),
                    lines=lines,
                )
            )
        return stops


class AreaSession:
    """Tracks the active loaded area for MCP tool calls within a process."""

    def __init__(self) -> None:
        self.graph: NetworkGraph | None = None
        self.bbox: BoundingBox | None = None
        self.label: str | None = None
        self.line_index: dict[str, list[str]] = defaultdict(list)
