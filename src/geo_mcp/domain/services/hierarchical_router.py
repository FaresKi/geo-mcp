from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.network import EdgeMode, NetworkGraph
from geo_mcp.domain.model.route import Itinerary, RouteLeg
from geo_mcp.domain.services.pathfinding import PathfindingError, PathfindingService
from geo_mcp.domain.services.routing_strategy import (
    RoutingStrategy,
    RoutingStrategySelector,
)
from geo_mcp.domain.services.transit_costs import stop_rail_bonus
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.graph.tile_store import TileGraphStore
from geo_mcp.infrastructure.osm.network_repository import AreaSession
from geo_mcp.infrastructure.resilience.deadline import Deadline

logger = logging.getLogger(__name__)

ProgressFn = Callable[[float, float | None, str], None]


@dataclass
class AdaptiveRouter:
    """Distance-adaptive router: local multimodal A* or hierarchical transit-first."""

    settings: Settings
    tiles: TileGraphStore
    pathfinder: PathfindingService
    selector: RoutingStrategySelector
    session: AreaSession

    def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        modes: list[str] | None = None,
        force_strategy: RoutingStrategy | None = None,
        deadline: Deadline | None = None,
        on_progress: ProgressFn | None = None,
    ) -> Itinerary:
        decision = self.selector.decide(
            origin, destination, modes=modes, force=force_strategy
        )
        self._progress(on_progress, 5, 100, f"strategy={decision.strategy.value} ({decision.reason})")

        allowed = self._map_modes(modes)
        if decision.strategy == RoutingStrategy.LOCAL:
            return self._route_local(
                origin,
                destination,
                allowed_modes=allowed,
                deadline=deadline,
                on_progress=on_progress,
            )
        return self._route_hierarchical(
            origin,
            destination,
            allowed_modes=allowed,
            deadline=deadline,
            on_progress=on_progress,
        )

    def _route_local(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        allowed_modes: list[EdgeMode] | None,
        deadline: Deadline | None,
        on_progress: ProgressFn | None,
    ) -> Itinerary:
        # Prefer already-loaded session graph when both points are inside.
        graph = self.session.graph
        if (
            graph is not None
            and self.session.bbox is not None
            and self.session.bbox.contains(origin)
            and self.session.bbox.contains(destination)
        ):
            self._progress(on_progress, 40, 100, "Using loaded area graph")
        else:
            self._progress(on_progress, 15, 100, "Loading local walk+transit tiles")
            bbox = BoundingBox.from_points(
                origin, destination, pad_deg=self.settings.tile_size_deg
            )
            tiles = self.tiles.tiles_for_bbox(bbox, max_tiles=self.settings.max_walk_tiles)
            graph = self.tiles.load_tiles(
                tiles,
                layer="full",
                deadline=deadline,
                on_progress=lambda d, t, m: self._progress(
                    on_progress, 15 + 50 * (d / max(t, 1)), 100, m
                ),
            )
            self._update_session(graph, label="local_tiles")

        self._progress(on_progress, 80, 100, "Running local A*")
        itinerary = self.pathfinder.find_path(
            graph,
            origin,
            destination,
            allowed_modes=allowed_modes,
            max_snap_m=self.settings.max_snap_m,
        )
        self._progress(on_progress, 100, 100, "Route ready")
        return itinerary

    def _route_hierarchical(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        allowed_modes: list[EdgeMode] | None,
        deadline: Deadline | None,
        on_progress: ProgressFn | None,
    ) -> Itinerary:
        modes = set(allowed_modes or (EdgeMode.WALK, EdgeMode.TRANSIT, EdgeMode.TRANSFER))
        if EdgeMode.TRANSIT not in modes:
            # Hierarchical needs transit; fall back to expanding local tiles.
            return self._route_local(
                origin,
                destination,
                allowed_modes=allowed_modes,
                deadline=deadline,
                on_progress=on_progress,
            )

        pad = self.settings.transit_bbox_pad_deg
        corridor = BoundingBox.from_points(origin, destination, pad_deg=pad)
        corridor = self._widen_corridor(corridor)
        self._progress(on_progress, 10, 100, "Fetching transit skeleton")
        if deadline is not None:
            deadline.check("transit_skeleton")
        transit_graph, stops, _lines = self.tiles.load_transit_skeleton(
            corridor, deadline=deadline
        )
        if not stops:
            raise PathfindingError("No transit stops found in corridor")

        access = self._select_access_stops(transit_graph, origin)
        egress = self._select_access_stops(transit_graph, destination)
        if not access or not egress:
            raise PathfindingError(
                "Could not find access/egress stops within search radius"
            )

        self._progress(on_progress, 35, 100, "Searching transit network")
        transit_modes = {EdgeMode.TRANSIT, EdgeMode.TRANSFER}
        sources = {
            node.id: self._access_seed_cost(node, dist) for node, dist in access
        }
        goals = {node.id for node, _ in egress}
        transit_itin = self.pathfinder.find_path_between_sets(
            transit_graph,
            sources,
            goals,
            allowed_modes=transit_modes,
            goal_point=destination,
        )

        board_id = transit_itin.node_ids[0] if transit_itin.node_ids else access[0][0].id
        alight_id = transit_itin.node_ids[-1] if transit_itin.node_ids else egress[0][0].id
        board_stop = transit_graph.nodes[board_id]
        alight_stop = transit_graph.nodes[alight_id]

        self._progress(on_progress, 55, 100, "Loading origin walk tiles")
        walk_origin = self._safe_walk_tile(origin, deadline, on_progress, base=55)
        self._progress(on_progress, 70, 100, "Loading destination walk tiles")
        walk_dest = self._safe_walk_tile(destination, deadline, on_progress, base=70)

        access_leg = self._walk_or_direct(
            walk_origin, origin, board_stop.point, board_stop.name or board_id
        )
        egress_leg = self._walk_or_direct(
            walk_dest, alight_stop.point, destination, "destination"
        )

        self._progress(on_progress, 90, 100, "Stitching itinerary")
        # Build synthetic walk legs when tile routing fails but geodesic is known.
        parts: list[Itinerary] = []
        if access_leg is not None:
            parts.append(access_leg)
        parts.append(transit_itin)
        if egress_leg is not None:
            parts.append(egress_leg)
        itinerary = self.pathfinder.stitch(*parts)
        self._update_session(transit_graph, label="hierarchical_corridor")
        self._progress(on_progress, 100, 100, "Route ready")
        return itinerary

    def _widen_corridor(self, bbox: BoundingBox) -> BoundingBox:
        """Ensure the transit clip is wide enough to include hub stations."""
        min_span = getattr(self.settings, "transit_min_span_deg", 0.08)
        lat_span, lon_span = bbox.spans()
        south, west, north, east = bbox.south, bbox.west, bbox.north, bbox.east
        if lat_span < min_span:
            mid = (south + north) / 2
            half = min_span / 2
            south, north = mid - half, mid + half
        if lon_span < min_span:
            mid = (west + east) / 2
            half = min_span / 2
            west, east = mid - half, mid + half
        return BoundingBox(south=south, west=west, north=north, east=east)

    def _select_access_stops(
        self,
        transit_graph: NetworkGraph,
        point: GeoPoint,
        *,
        limit: int = 16,
    ) -> list[tuple]:
        radius = self.settings.access_radius_m * 2
        candidates = transit_graph.nearest_stops(
            point, limit=max(limit * 2, 24), max_radius_m=radius
        )
        if not candidates:
            return []
        if not getattr(self.settings, "prefer_rail_access", True):
            return candidates[:limit]

        rail = [
            (n, d)
            for n, d in candidates
            if stop_rail_bonus(n.tags.get("modes"))
        ]
        other = [
            (n, d)
            for n, d in candidates
            if not stop_rail_bonus(n.tags.get("modes"))
        ]
        # Prefer rail stops that actually expose a line ref (skip bare stations).
        rail.sort(
            key=lambda nd: (
                0 if (nd[0].tags.get("lines") or "").strip() else 1,
                nd[1],
            )
        )
        ordered = rail + other
        return ordered[:limit]

    def _access_seed_cost(self, node, dist_m: float) -> float:
        """Walk time to stop, with a bonus for rail/metro boarding."""
        cost = dist_m / self.settings.walk_speed_mps
        if getattr(self.settings, "prefer_rail_access", True) and stop_rail_bonus(
            node.tags.get("modes")
        ):
            cost *= 0.35  # strongly prefer RER/metro boarding
            # Prefer stops tagged with real line refs (B, 14, …) over empty hubs.
            if (node.tags.get("lines") or "").strip():
                cost *= 0.7
            # Orlyval / airport shuttles are poor access for city trips.
            lines = (node.tags.get("lines") or "").lower()
            if "orlyval" in lines:
                cost *= 4.0
        return cost

    def _safe_walk_tile(
        self,
        point: GeoPoint,
        deadline: Deadline | None,
        on_progress: ProgressFn | None,
        *,
        base: float,
    ) -> NetworkGraph | None:
        try:
            return self.tiles.load_walk_around(
                point,
                ring=1,
                deadline=deadline,
                on_progress=lambda d, t, m: self._progress(
                    on_progress, base + 10 * (d / max(t, 1)), 100, m
                ),
            )
        except Exception as exc:
            logger.warning("Walk tile load degraded: %s", exc)
            return None

    def _walk_or_direct(
        self,
        graph: NetworkGraph | None,
        origin: GeoPoint,
        destination: GeoPoint,
        dest_name: str,
    ) -> Itinerary | None:
        dist = origin.distance_meters(destination)
        if dist < 5:
            return None
        if graph is not None and graph.node_count > 0:
            try:
                return self.pathfinder.find_path(
                    graph,
                    origin,
                    destination,
                    allowed_modes=[EdgeMode.WALK],
                    max_snap_m=self.settings.max_snap_m,
                )
            except PathfindingError:
                pass
        # Geodesic walk fallback so hierarchical routes still complete.
        duration = dist / self.settings.walk_speed_mps
        leg = RouteLeg(
            mode=EdgeMode.WALK,
            distance_m=dist,
            duration_s=duration,
            from_name=None,
            to_name=dest_name,
            geometry=(origin, destination),
            instruction=f"Walk {dist:.0f} m to {dest_name}",
        )
        return Itinerary(
            legs=(leg,),
            total_distance_m=dist,
            total_duration_s=duration,
            transfer_count=0,
            narrative=leg.instruction,
            modes_used=("walk",),
        )

    def _update_session(self, graph: NetworkGraph, *, label: str) -> None:
        self.session.graph = graph
        self.session.bbox = graph.bbox
        self.session.label = label

    def _map_modes(self, modes: list[str] | None) -> list[EdgeMode] | None:
        if not modes:
            return None
        mapped: list[EdgeMode] = []
        for mode in modes:
            key = mode.lower().strip()
            if key in {"walk", "walking", "foot"}:
                mapped.append(EdgeMode.WALK)
            elif key in {"transit", "bus", "rail", "tram", "subway", "train", "ferry"}:
                mapped.append(EdgeMode.TRANSIT)
                mapped.append(EdgeMode.TRANSFER)
            elif key == "transfer":
                mapped.append(EdgeMode.TRANSFER)
            else:
                raise ValueError(f"Unknown mode: {mode}")
        return mapped

    def _progress(
        self,
        on_progress: ProgressFn | None,
        progress: float,
        total: float | None,
        message: str,
    ) -> None:
        if on_progress is not None:
            on_progress(progress, total, message)
