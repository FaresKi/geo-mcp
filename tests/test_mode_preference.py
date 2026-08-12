from __future__ import annotations

from unittest.mock import MagicMock

from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.domain.services.pathfinding import PathfindingService
from geo_mcp.domain.services.transit_costs import speed_for_route_mode
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.osm.network_repository import (
    OsmNetworkRepository,
    station_key,
)


def _node(nid: str, lat: float, lon: float, *, kind: str = "stop", modes: str = "") -> GraphNode:
    return GraphNode(
        id=nid,
        point=GeoPoint(lat, lon),
        kind=kind,
        name=nid,
        tags={"modes": modes} if modes else {},
    )


def _transit(src: str, tgt: str, *, length: float, line: str, route: str, speed: float) -> GraphEdge:
    return GraphEdge(
        source_id=src,
        target_id=tgt,
        mode=EdgeMode.TRANSIT,
        length_m=length,
        travel_time_s=length / speed,
        line_ref=line,
        name=line,
        tags={"route": route},
    )


def test_speed_for_route_mode_prefers_rail() -> None:
    assert speed_for_route_mode("train") > speed_for_route_mode("subway")
    assert speed_for_route_mode("subway") > speed_for_route_mode("bus")
    assert speed_for_route_mode("ferry", ferry_mps=6.5) == 6.5


def test_line_change_penalty_avoids_hopscotch() -> None:
    """
    Direct train A→C is longer than A→B→C via two buses, but buses require a line change.
    With a strong line-change penalty, train should win.
    """
    g = NetworkGraph()
    for n in (
        _node("A", 0.0, 0.0),
        _node("B", 0.0, 0.05),
        _node("C", 0.0, 0.10),
    ):
        g.add_node(n)

    bus_speed = speed_for_route_mode("bus")
    train_speed = speed_for_route_mode("train")
    # Two short bus hops with a line change at B.
    for edge in (
        _transit("A", "B", length=4000, line="bus1", route="bus", speed=bus_speed),
        _transit("B", "C", length=4000, line="bus2", route="bus", speed=bus_speed),
        # Longer single train ride.
        _transit("A", "C", length=12000, line="RER", route="train", speed=train_speed),
        _transit("C", "A", length=12000, line="RER", route="train", speed=train_speed),
    ):
        g.add_edge(edge)

    pf = PathfindingService(line_change_penalty_s=300.0)
    itin = pf.find_path_between_sets(
        g,
        {"A": 0.0},
        {"C"},
        allowed_modes=[EdgeMode.TRANSIT],
    )
    lines = [leg.line_ref for leg in itin.legs if leg.line_ref]
    assert lines == ["RER"]
    assert itin.transfer_count == 0


def test_station_key_normalizes_rer_suffix() -> None:
    assert station_key("Antony RER") == station_key("Antony")
    assert station_key("Châtelet - Les Halles").startswith("chatelet")


def test_stop_transfers_connect_rail_interchange() -> None:
    settings = Settings(stop_transfer_snap_m=400.0)
    repo = OsmNetworkRepository(settings, overpass=MagicMock())
    g = NetworkGraph()
    g.add_node(
        GraphNode(
            id="rer",
            point=GeoPoint(48.86, 2.35),
            kind="stop",
            name="Châtelet - Les Halles",
            tags={"modes": "train,stop_position", "lines": "B"},
        )
    )
    g.add_node(
        GraphNode(
            id="metro",
            point=GeoPoint(48.8605, 2.3505),  # ~70 m away
            kind="stop",
            name="Châtelet",
            tags={"modes": "subway,stop_position", "lines": "14"},
        )
    )
    repo._link_stop_transfers(g)
    xfer = [
        e
        for e in g.neighbors("rer")
        if e.mode == EdgeMode.TRANSFER and e.target_id == "metro"
    ]
    assert xfer, "expected transfer between RER and metro platforms"


def test_rail_faster_than_bus_same_geometry() -> None:
    g = NetworkGraph()
    g.add_node(_node("A", 0.0, 0.0))
    g.add_node(_node("C", 0.0, 0.1))
    bus_speed = speed_for_route_mode("bus")
    train_speed = speed_for_route_mode("train")
    length = 10_000.0
    g.add_edge(
        _transit("A", "C", length=length, line="busX", route="bus", speed=bus_speed)
    )
    g.add_edge(
        _transit("A", "C", length=length, line="RER", route="train", speed=train_speed)
    )
    pf = PathfindingService()
    itin = pf.find_path_between_sets(
        g, {"A": 0.0}, {"C"}, allowed_modes=[EdgeMode.TRANSIT]
    )
    assert any(leg.line_ref == "RER" for leg in itin.legs)
