from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict

import osmnx as ox

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.domain.model.transit import TransitLine, TransitStop
from geo_mcp.domain.services.transit_costs import (
    speed_for_route_mode,
    stop_rail_bonus,
)
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.graph.cache import DiskGraphCache
from geo_mcp.infrastructure.osm.overpass import OverpassClient
from geo_mcp.infrastructure.osm.pbf_builder import PbfNetworkBuilder

logger = logging.getLogger(__name__)

_RAIL_MODES = frozenset({"train", "subway", "light_rail"})


def station_key(name: str | None) -> str:
    """Normalize stop names so 'Antony RER' and 'Antony' cluster together."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"\b(rer|metro|métro|station|gare)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


class OsmNetworkRepository:
    """Builds a multimodal walk+transit NetworkGraph from OSM and caches it."""

    def __init__(
        self,
        settings: Settings,
        overpass: OverpassClient,
        cache: DiskGraphCache | None = None,
        pbf: PbfNetworkBuilder | None = None,
    ) -> None:
        self._settings = settings
        self._overpass = overpass
        self._cache = cache or DiskGraphCache(settings)
        self._pbf = pbf
        self._memory: dict[str, NetworkGraph] = {}
        # Latest loaded graph + transit catalog for use cases that need stops/lines.
        self.current_graph: NetworkGraph | None = None
        self.current_stops: list[TransitStop] = []
        self.current_lines: list[TransitLine] = []

    def using_pbf(self) -> bool:
        return bool(
            self._pbf
            and self._settings.prefer_pbf
            and self._pbf.available()
        )

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

        logger.info(
            "Fetching walk network for bbox %s (source=%s)",
            key,
            "pbf" if self.using_pbf() else "overpass",
        )
        walk = self._load_walk_graph(bbox)

        stops: list[TransitStop] = []
        lines: list[TransitLine] = []
        try:
            stops, lines = self.fetch_transit(bbox)
            self._attach_transit(walk, stops, lines)
        except Exception:
            if self._settings.offline:
                raise
            logger.exception(
                "Transit overlay failed for %s; continuing with walk-only graph",
                key,
            )

        if not self._settings.offline and not self.using_pbf():
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
        stops, lines = self.fetch_transit(bbox)
        self.current_stops = stops
        self.current_lines = lines
        return lines

    def fetch_transit(
        self, bbox: BoundingBox
    ) -> tuple[list[TransitStop], list[TransitLine]]:
        if self.using_pbf():
            assert self._pbf is not None
            return self._pbf.fetch_transit(bbox)
        if self._settings.offline:
            raise RuntimeError(
                "offline=true but no local PBF available for transit. "
                "Run: geo-mcp ingest"
            )
        return self._overpass.fetch_transit(bbox)

    def _load_walk_graph(self, bbox: BoundingBox) -> NetworkGraph:
        if self.using_pbf():
            assert self._pbf is not None
            return self._pbf.build_walk_graph(bbox)
        if self._settings.offline:
            raise RuntimeError(
                "offline=true but no local PBF available for walk graph. "
                "Run: geo-mcp ingest"
            )
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
                speed = speed_for_route_mode(
                    line.mode,
                    default_mps=self._settings.transit_speed_mps,
                    ferry_mps=self._settings.ferry_speed_mps,
                )
                travel = length / speed
                if line.mode in {"bus", "trolleybus"}:
                    travel *= getattr(self._settings, "bus_time_factor", 2.0)
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

        self._link_rail_by_ref(graph, lines)
        self._link_stop_transfers(graph)

    def _link_rail_by_ref(
        self,
        graph: NetworkGraph,
        lines: list[TransitLine],
    ) -> None:
        """Connect fragmented RER/metro branches by sorting stops on each ref."""
        by_ref: dict[tuple[str, str], set[str]] = defaultdict(set)
        for line in lines:
            if line.mode not in _RAIL_MODES or not line.ref:
                continue
            for sid in line.stop_ids:
                if sid in graph.nodes:
                    by_ref[(line.mode, line.ref)].add(sid)

        # Also include stops tagged with a rail line even if missing from the
        # clipped relation member list (common for Châtelet-Les Halles).
        for node in graph.nodes.values():
            if node.kind != "stop":
                continue
            modes = {
                m.strip()
                for m in (node.tags.get("modes") or "").split(",")
                if m.strip()
            }
            refs = [
                r.strip()
                for r in (node.tags.get("lines") or "").split(",")
                if r.strip()
            ]
            rail_modes = modes & _RAIL_MODES
            if not rail_modes or not refs:
                continue
            for mode in rail_modes:
                for ref in refs:
                    by_ref[(mode, ref)].add(node.id)

        for (mode, ref), stop_ids in by_ref.items():
            nodes = [graph.nodes[sid] for sid in stop_ids if sid in graph.nodes]
            if len(nodes) < 2:
                continue
            # Sort along dominant axis (RER B is N–S).
            lats = [n.point.lat for n in nodes]
            lons = [n.point.lon for n in nodes]
            if max(lats) - min(lats) >= max(lons) - min(lons):
                ordered = sorted(nodes, key=lambda n: n.point.lat)
            else:
                ordered = sorted(nodes, key=lambda n: n.point.lon)
            speed = speed_for_route_mode(
                mode,
                default_mps=self._settings.transit_speed_mps,
                ferry_mps=self._settings.ferry_speed_mps,
            )
            for a, b in zip(ordered, ordered[1:], strict=False):
                length = a.point.distance_meters(b.point)
                if length <= 0 or length > 12_000:
                    continue
                travel = length / speed
                for src, tgt in ((a.id, b.id), (b.id, a.id)):
                    graph.add_edge(
                        GraphEdge(
                            source_id=src,
                            target_id=tgt,
                            mode=EdgeMode.TRANSIT,
                            length_m=length,
                            travel_time_s=travel,
                            name=ref,
                            line_ref=ref,
                            tags={"route": mode, "linked": "rail_axis"},
                        )
                    )

    def _link_stop_transfers(self, graph: NetworkGraph) -> None:
        """Link nearby platforms so RER↔metro (and bus) transfers work."""
        radius = float(getattr(self._settings, "stop_transfer_snap_m", 400.0))
        walk = self._settings.walk_speed_mps
        stops = [n for n in graph.nodes.values() if n.kind == "stop"]
        if len(stops) < 2:
            return

        graph.ensure_index()
        seen: set[tuple[str, str]] = set()

        def add_pair(a: GraphNode, b: GraphNode, dist: float) -> None:
            key = (a.id, b.id) if a.id < b.id else (b.id, a.id)
            if key in seen or dist < 0.5:
                return
            seen.add(key)
            travel = dist / walk
            for src, tgt in ((a.id, b.id), (b.id, a.id)):
                graph.add_edge(
                    GraphEdge(
                        source_id=src,
                        target_id=tgt,
                        mode=EdgeMode.TRANSFER,
                        length_m=dist,
                        travel_time_s=travel,
                        name="transfer",
                        tags={"linked": "stop_transfer"},
                    )
                )

        for stop in stops:
            nearby = graph.nearest_stops(
                stop.point, limit=24, max_radius_m=radius
            )
            stop_rail = stop_rail_bonus(stop.tags.get("modes"))
            stop_key = station_key(stop.name)
            for other, dist in nearby:
                if other.id == stop.id:
                    continue
                other_rail = stop_rail_bonus(other.tags.get("modes"))
                other_key = station_key(other.name)
                same = bool(stop_key) and stop_key == other_key
                if (
                    not same
                    and stop_rail
                    and other_rail
                    and stop_key
                    and other_key
                    and (stop_key in other_key or other_key in stop_key)
                ):
                    same = True
                if stop_rail or other_rail or same:
                    add_pair(stop, other, dist)
                elif dist <= 80.0:
                    # Tight bus platform pairs only.
                    add_pair(stop, other, dist)

        # Same-name clusters beyond pure kNN (large underground complexes).
        by_name: dict[str, list[GraphNode]] = defaultdict(list)
        for stop in stops:
            key = station_key(stop.name)
            if key:
                by_name[key].append(stop)
        name_radius = max(radius, 550.0)
        for group in by_name.values():
            if len(group) < 2:
                continue
            for i, a in enumerate(group):
                for b in group[i + 1 :]:
                    dist = a.point.distance_meters(b.point)
                    if dist <= name_radius:
                        add_pair(a, b, dist)

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
