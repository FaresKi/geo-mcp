from __future__ import annotations

import atexit
from dataclasses import dataclass

from geo_mcp.application.services.adaptive_router import AdaptiveRouter
from geo_mcp.application.services.job_service import InMemoryJobStore, JobService
from geo_mcp.application.session import AreaSession
from geo_mcp.application.use_cases import (
    DescribeAreaUseCase,
    FindNearbyUseCase,
    GeocodeUseCase,
    ListTransitOptionsUseCase,
    LoadAreaUseCase,
    PlanRouteUseCase,
    ReverseGeocodeUseCase,
    SnapToNetworkUseCase,
)
from geo_mcp.domain.services.pathfinding import PathfindingService
from geo_mcp.domain.services.routing_strategy import RoutingStrategySelector
from geo_mcp.infrastructure.config import Settings, get_settings
from geo_mcp.infrastructure.graph.cache import DiskGraphCache
from geo_mcp.infrastructure.graph.tile_store import TileGraphStore
from geo_mcp.infrastructure.osm.network_repository import OsmNetworkRepository
from geo_mcp.infrastructure.osm.nominatim import NominatimGeocoder
from geo_mcp.infrastructure.osm.overpass import OverpassClient
from geo_mcp.infrastructure.osm.pbf_builder import PbfNetworkBuilder
from geo_mcp.infrastructure.osm.pbf_store import PbfStore


@dataclass
class AppContainer:
    settings: Settings
    geocoder: NominatimGeocoder
    overpass: OverpassClient
    networks: OsmNetworkRepository
    tiles: TileGraphStore
    session: AreaSession
    pathfinder: PathfindingService
    router: AdaptiveRouter
    jobs: JobService
    load_area: LoadAreaUseCase
    geocode: GeocodeUseCase
    reverse_geocode: ReverseGeocodeUseCase
    plan_route: PlanRouteUseCase
    find_nearby: FindNearbyUseCase
    list_transit_options: ListTransitOptionsUseCase
    describe_area: DescribeAreaUseCase
    snap_to_network: SnapToNetworkUseCase

    def close(self) -> None:
        self.jobs.close()
        self.tiles.close()
        self.geocoder.close()
        self.overpass.close()


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or get_settings()
    routing = settings.routing_config()
    geocoder = NominatimGeocoder(settings)
    overpass = OverpassClient(settings)
    cache = DiskGraphCache(settings)
    pbf_store = PbfStore(settings)
    pbf_builder = PbfNetworkBuilder(settings, pbf_store)
    networks = OsmNetworkRepository(settings, overpass, cache, pbf=pbf_builder)
    tiles = TileGraphStore(settings, networks)
    session = AreaSession()
    pathfinder = PathfindingService(
        walk_speed_mps=settings.walk_speed_mps,
        transit_speed_mps=settings.transit_speed_mps,
        transfer_penalty_s=settings.transfer_penalty_s,
        line_change_penalty_s=settings.line_change_penalty_s,
    )
    selector = RoutingStrategySelector(local_max_m=routing.local_max_m)
    router = AdaptiveRouter(
        config=routing,
        tiles=tiles,
        pathfinder=pathfinder,
        selector=selector,
        session=session,
    )
    jobs = JobService(
        InMemoryJobStore(),
        max_workers=max(2, settings.max_workers // 2),
        ttl_s=settings.job_ttl_s,
    )
    container = AppContainer(
        settings=settings,
        geocoder=geocoder,
        overpass=overpass,
        networks=networks,
        tiles=tiles,
        session=session,
        pathfinder=pathfinder,
        router=router,
        jobs=jobs,
        load_area=LoadAreaUseCase(geocoder, networks, tiles, session, routing),
        geocode=GeocodeUseCase(geocoder),
        reverse_geocode=ReverseGeocodeUseCase(geocoder),
        plan_route=PlanRouteUseCase(router, jobs, routing),
        find_nearby=FindNearbyUseCase(overpass),
        list_transit_options=ListTransitOptionsUseCase(networks, session),
        describe_area=DescribeAreaUseCase(session, overpass),
        snap_to_network=SnapToNetworkUseCase(session, tiles, routing),
    )
    atexit.register(container.close)
    return container
