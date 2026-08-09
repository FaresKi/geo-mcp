from __future__ import annotations

from dataclasses import dataclass

from geo_mcp.application.dto import ItineraryDTO, SnapResultDTO
from geo_mcp.application.mappers import itinerary_to_dto, point_to_dto
from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.network import EdgeMode
from geo_mcp.domain.services.pathfinding import PathfindingError, PathfindingService
from geo_mcp.infrastructure.osm.network_repository import AreaSession


@dataclass
class PlanRouteUseCase:
    session: AreaSession
    pathfinder: PathfindingService

    def execute(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        modes: list[str] | None = None,
    ) -> ItineraryDTO:
        graph = self.session.graph
        if graph is None:
            raise RuntimeError("No area loaded. Call load_area first.")

        allowed = None
        if modes:
            mapped: list[EdgeMode] = []
            for mode in modes:
                key = mode.lower().strip()
                if key in {"walk", "walking", "foot"}:
                    mapped.append(EdgeMode.WALK)
                elif key in {"transit", "bus", "rail", "tram", "subway"}:
                    mapped.append(EdgeMode.TRANSIT)
                    mapped.append(EdgeMode.TRANSFER)
                elif key == "transfer":
                    mapped.append(EdgeMode.TRANSFER)
                else:
                    raise ValueError(f"Unknown mode: {mode}")
            allowed = mapped

        try:
            itinerary = self.pathfinder.find_path(
                graph,
                GeoPoint(lat=origin_lat, lon=origin_lon),
                GeoPoint(lat=destination_lat, lon=destination_lon),
                allowed_modes=allowed,
            )
        except PathfindingError as exc:
            raise RuntimeError(str(exc)) from exc
        return itinerary_to_dto(itinerary)


@dataclass
class SnapToNetworkUseCase:
    session: AreaSession

    def execute(self, *, lat: float, lon: float, kind: str | None = None) -> SnapResultDTO:
        graph = self.session.graph
        if graph is None:
            raise RuntimeError("No area loaded. Call load_area first.")
        match = graph.nearest_node(GeoPoint(lat=lat, lon=lon), kind=kind)
        if match is None:
            raise RuntimeError("No nodes available in loaded graph")
        node, dist = match
        return SnapResultDTO(
            node_id=node.id,
            kind=node.kind,
            name=node.name,
            point=point_to_dto(node.point),
            distance_m=dist,
        )
