from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from geo_mcp.application.dto import ItineraryDTO, SnapResultDTO
from geo_mcp.application.mappers import itinerary_to_dto, point_to_dto
from geo_mcp.application.services.job_service import Job, JobService
from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.services.hierarchical_router import AdaptiveRouter
from geo_mcp.domain.services.pathfinding import PathfindingError
from geo_mcp.domain.services.routing_strategy import RoutingStrategy
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.osm.network_repository import AreaSession
from geo_mcp.infrastructure.resilience.deadline import Deadline


@dataclass
class PlanRouteUseCase:
    router: AdaptiveRouter
    jobs: JobService
    settings: Settings

    def execute(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        modes: list[str] | None = None,
        defer: bool = False,
        force_strategy: str | None = None,
        on_progress: Any | None = None,
    ) -> ItineraryDTO | dict[str, Any]:
        origin = GeoPoint(lat=origin_lat, lon=origin_lon)
        destination = GeoPoint(lat=destination_lat, lon=destination_lon)
        distance_m = origin.distance_meters(destination)
        should_defer = defer or distance_m > self.settings.local_max_m * 3

        forced = None
        if force_strategy:
            forced = RoutingStrategy(force_strategy)

        if should_defer:
            job = self.jobs.submit(
                "plan_route",
                lambda job: self._run_job(
                    job,
                    origin=origin,
                    destination=destination,
                    modes=modes,
                    forced=forced,
                ),
            )
            return job.to_dict()

        def progress(p: float, total: float | None, message: str) -> None:
            if on_progress is not None:
                on_progress(p, total, message)

        try:
            itinerary = self.router.route(
                origin,
                destination,
                modes=modes,
                force_strategy=forced,
                deadline=Deadline.from_seconds(self.settings.operation_deadline_s),
                on_progress=progress,
            )
        except PathfindingError as exc:
            raise RuntimeError(str(exc)) from exc
        return itinerary_to_dto(itinerary)

    def submit(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        modes: list[str] | None = None,
        force_strategy: str | None = None,
    ) -> dict[str, Any]:
        return self.execute(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            destination_lat=destination_lat,
            destination_lon=destination_lon,
            modes=modes,
            defer=True,
            force_strategy=force_strategy,
        )  # type: ignore[return-value]

    def _run_job(
        self,
        job: Job,
        *,
        origin: GeoPoint,
        destination: GeoPoint,
        modes: list[str] | None,
        forced: RoutingStrategy | None,
    ) -> dict[str, Any]:
        def progress(p: float, total: float | None, message: str) -> None:
            self.jobs.report(job, p, total, message)

        itinerary = self.router.route(
            origin,
            destination,
            modes=modes,
            force_strategy=forced,
            deadline=Deadline.from_seconds(self.settings.operation_deadline_s),
            on_progress=progress,
        )
        return itinerary_to_dto(itinerary).model_dump()


@dataclass
class SnapToNetworkUseCase:
    session: AreaSession
    router: AdaptiveRouter
    settings: Settings

    def execute(self, *, lat: float, lon: float, kind: str | None = None) -> SnapResultDTO:
        point = GeoPoint(lat=lat, lon=lon)
        graph = self.session.graph
        if graph is None or (
            self.session.bbox is not None and not self.session.bbox.contains(point)
        ):
            graph = self.router.tiles.load_walk_around(point, ring=0)
            self.session.graph = graph
            self.session.bbox = graph.bbox
            self.session.label = self.session.label or "snap_tiles"
        match = graph.nearest_node(point, kind=kind)
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
