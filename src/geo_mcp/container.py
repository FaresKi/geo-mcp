from __future__ import annotations

from dataclasses import dataclass

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
from geo_mcp.infrastructure.config import Settings, get_settings
from geo_mcp.infrastructure.graph.cache import DiskGraphCache
from geo_mcp.infrastructure.osm.network_repository import AreaSession, OsmNetworkRepository
from geo_mcp.infrastructure.osm.nominatim import NominatimGeocoder
from geo_mcp.infrastructure.osm.overpass import OverpassClient


@dataclass
class AppContainer:
    settings: Settings
    geocoder: NominatimGeocoder
    overpass: OverpassClient
    networks: OsmNetworkRepository
    session: AreaSession
    pathfinder: PathfindingService
    load_area: LoadAreaUseCase
    geocode: GeocodeUseCase
    reverse_geocode: ReverseGeocodeUseCase
    plan_route: PlanRouteUseCase
    find_nearby: FindNearbyUseCase
    list_transit_options: ListTransitOptionsUseCase
    describe_area: DescribeAreaUseCase
    snap_to_network: SnapToNetworkUseCase

    def close(self) -> None:
        self.geocoder.close()
        self.overpass.close()


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or get_settings()
    geocoder = NominatimGeocoder(settings)
    overpass = OverpassClient(settings)
    cache = DiskGraphCache(settings)
    networks = OsmNetworkRepository(settings, overpass, cache)
    session = AreaSession()
    pathfinder = PathfindingService(
        walk_speed_mps=settings.walk_speed_mps,
        transit_speed_mps=settings.transit_speed_mps,
        transfer_penalty_s=settings.transfer_penalty_s,
    )
    return AppContainer(
        settings=settings,
        geocoder=geocoder,
        overpass=overpass,
        networks=networks,
        session=session,
        pathfinder=pathfinder,
        load_area=LoadAreaUseCase(geocoder, networks, session, settings),
        geocode=GeocodeUseCase(geocoder),
        reverse_geocode=ReverseGeocodeUseCase(geocoder),
        plan_route=PlanRouteUseCase(session, pathfinder),
        find_nearby=FindNearbyUseCase(overpass),
        list_transit_options=ListTransitOptionsUseCase(networks, session),
        describe_area=DescribeAreaUseCase(session, overpass, networks),
        snap_to_network=SnapToNetworkUseCase(session),
    )
