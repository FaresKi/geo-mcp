from __future__ import annotations

from dataclasses import dataclass, field

from geo_mcp.application.services.job_service import InMemoryJobStore, JobService
from geo_mcp.application.use_cases.routing import PlanRouteUseCase
from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.domain.model.transit import TransitLine, TransitStop
from geo_mcp.domain.services.hierarchical_router import AdaptiveRouter
from geo_mcp.domain.services.pathfinding import PathfindingService
from geo_mcp.domain.services.routing_strategy import RoutingStrategySelector
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.osm.network_repository import AreaSession


def _stop(sid: str, lat: float, lon: float, name: str) -> GraphNode:
    return GraphNode(id=sid, point=GeoPoint(lat, lon), kind="stop", name=name)


def _walk_node(nid: str, lat: float, lon: float) -> GraphNode:
    return GraphNode(id=nid, point=GeoPoint(lat, lon), kind="intersection", name=nid)


@dataclass
class FakeTileStore:
    walk_graphs: dict[str, NetworkGraph] = field(default_factory=dict)
    transit_graph: NetworkGraph | None = None
    stops: list[TransitStop] = field(default_factory=list)
    lines: list[TransitLine] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def tiles_for_point(self, point: GeoPoint, *, ring: int = 0):
        return []

    def tiles_for_bbox(self, bbox: BoundingBox, *, max_tiles: int | None = None):
        return []

    def load_tiles(self, tiles, *, layer="full", force_refresh=False, deadline=None, on_progress=None):
        self.calls.append(f"load_tiles:{layer}")
        g = NetworkGraph()
        for graph in self.walk_graphs.values():
            g.merge_from(graph)
        if on_progress:
            on_progress(1, 1, "done")
        return g

    def load_walk_around(self, point, *, ring=1, deadline=None, on_progress=None):
        self.calls.append("walk_around")
        key = f"{round(point.lat, 2)}:{round(point.lon, 2)}"
        if key in self.walk_graphs:
            return self.walk_graphs[key]
        # nearest fake walk graph
        if self.walk_graphs:
            return next(iter(self.walk_graphs.values()))
        return NetworkGraph()

    def load_transit_skeleton(self, bbox, *, deadline=None):
        self.calls.append("transit_skeleton")
        assert self.transit_graph is not None
        return self.transit_graph, self.stops, self.lines


def _build_corridor_fakes() -> tuple[FakeTileStore, GeoPoint, GeoPoint]:
    origin = GeoPoint(0.0, 0.0)
    dest = GeoPoint(0.05, 0.0)  # ~5.5km > 2km local max

    transit = NetworkGraph(bbox=BoundingBox(0, -0.01, 0.06, 0.01))
    for node in (
        _stop("stop:o", 0.001, 0.0, "Origin Stop"),
        _stop("stop:m", 0.025, 0.0, "Mid Stop"),
        _stop("stop:d", 0.049, 0.0, "Dest Stop"),
    ):
        transit.add_node(node)
    for a, b, length in (
        ("stop:o", "stop:m", 2500),
        ("stop:m", "stop:d", 2500),
    ):
        for src, tgt in ((a, b), (b, a)):
            transit.add_edge(
                GraphEdge(
                    source_id=src,
                    target_id=tgt,
                    mode=EdgeMode.TRANSIT,
                    length_m=length,
                    travel_time_s=length / 8,
                    line_ref="RER",
                    name="RER",
                )
            )

    walk_o = NetworkGraph()
    walk_o.add_node(_walk_node("wo", 0.0, 0.0))
    walk_o.add_node(_walk_node("wo2", 0.001, 0.0))
    walk_o.add_edge(
        GraphEdge(
            source_id="wo",
            target_id="wo2",
            mode=EdgeMode.WALK,
            length_m=120,
            travel_time_s=80,
        )
    )
    walk_o.add_edge(
        GraphEdge(
            source_id="wo2",
            target_id="wo",
            mode=EdgeMode.WALK,
            length_m=120,
            travel_time_s=80,
        )
    )

    walk_d = NetworkGraph()
    walk_d.add_node(_walk_node("wd", 0.05, 0.0))
    walk_d.add_node(_walk_node("wd2", 0.049, 0.0))
    walk_d.add_edge(
        GraphEdge(
            source_id="wd2",
            target_id="wd",
            mode=EdgeMode.WALK,
            length_m=120,
            travel_time_s=80,
        )
    )
    walk_d.add_edge(
        GraphEdge(
            source_id="wd",
            target_id="wd2",
            mode=EdgeMode.WALK,
            length_m=120,
            travel_time_s=80,
        )
    )

    store = FakeTileStore(
        transit_graph=transit,
        stops=[
            TransitStop(id="stop:o", name="Origin Stop", point=GeoPoint(0.001, 0.0)),
            TransitStop(id="stop:m", name="Mid Stop", point=GeoPoint(0.025, 0.0)),
            TransitStop(id="stop:d", name="Dest Stop", point=GeoPoint(0.049, 0.0)),
        ],
        lines=[
            TransitLine(
                ref="RER",
                name="RER",
                mode="train",
                stop_ids=("stop:o", "stop:m", "stop:d"),
            )
        ],
        walk_graphs={
            "0.0:0.0": walk_o,
            "0.05:0.0": walk_d,
        },
    )
    return store, origin, dest


def test_hierarchical_route_stitches_transit() -> None:
    store, origin, dest = _build_corridor_fakes()
    settings = Settings(local_max_m=2000, access_radius_m=500)
    router = AdaptiveRouter(
        settings=settings,
        tiles=store,  # type: ignore[arg-type]
        pathfinder=PathfindingService(),
        selector=RoutingStrategySelector(local_max_m=2000),
        session=AreaSession(),
    )
    itin = router.route(origin, dest)
    assert any(leg.mode == EdgeMode.TRANSIT for leg in itin.legs)
    assert "transit_skeleton" in store.calls
    assert itin.total_duration_s > 0
    assert itin.narrative


def test_local_route_uses_session_graph() -> None:
    settings = Settings(local_max_m=5000)
    session = AreaSession()
    g = NetworkGraph(bbox=BoundingBox(-0.01, -0.01, 0.01, 0.01))
    g.add_node(_walk_node("a", 0.0, 0.0))
    g.add_node(_walk_node("b", 0.0, 0.001))
    g.add_edge(
        GraphEdge(
            source_id="a",
            target_id="b",
            mode=EdgeMode.WALK,
            length_m=100,
            travel_time_s=70,
        )
    )
    session.graph = g
    session.bbox = g.bbox
    store = FakeTileStore()
    router = AdaptiveRouter(
        settings=settings,
        tiles=store,  # type: ignore[arg-type]
        pathfinder=PathfindingService(),
        selector=RoutingStrategySelector(local_max_m=5000),
        session=session,
    )
    itin = router.route(GeoPoint(0.0, 0.0), GeoPoint(0.0, 0.001), modes=["walk"])
    assert itin.modes_used == ("walk",)
    assert store.calls == []  # used session graph


def test_plan_route_defers_long_trips() -> None:
    store, origin, dest = _build_corridor_fakes()
    # Auto-defer when distance > local_max_m * 3
    settings = Settings(local_max_m=1000)
    router = AdaptiveRouter(
        settings=settings,
        tiles=store,  # type: ignore[arg-type]
        pathfinder=PathfindingService(),
        selector=RoutingStrategySelector(local_max_m=1000),
        session=AreaSession(),
    )
    jobs = JobService(InMemoryJobStore(), max_workers=2)
    use_case = PlanRouteUseCase(router=router, jobs=jobs, settings=settings)
    result = use_case.execute(
        origin_lat=origin.lat,
        origin_lon=origin.lon,
        destination_lat=dest.lat,
        destination_lon=dest.lon,
    )
    assert isinstance(result, dict)
    assert "job_id" in result
    jobs.close()


def test_plan_route_sync_short() -> None:
    settings = Settings(local_max_m=5000)
    session = AreaSession()
    g = NetworkGraph(bbox=BoundingBox(-0.01, -0.01, 0.01, 0.01))
    g.add_node(_walk_node("a", 0.0, 0.0))
    g.add_node(_walk_node("b", 0.0, 0.001))
    g.add_edge(
        GraphEdge(
            source_id="a",
            target_id="b",
            mode=EdgeMode.WALK,
            length_m=100,
            travel_time_s=70,
        )
    )
    session.graph = g
    session.bbox = g.bbox
    router = AdaptiveRouter(
        settings=settings,
        tiles=FakeTileStore(),  # type: ignore[arg-type]
        pathfinder=PathfindingService(),
        selector=RoutingStrategySelector(local_max_m=5000),
        session=session,
    )
    use_case = PlanRouteUseCase(
        router=router,
        jobs=JobService(InMemoryJobStore(), max_workers=1),
        settings=settings,
    )
    result = use_case.execute(
        origin_lat=0.0,
        origin_lon=0.0,
        destination_lat=0.0,
        destination_lon=0.001,
        modes=["walk"],
    )
    assert result.total_distance_m > 0
    use_case.jobs.close()
