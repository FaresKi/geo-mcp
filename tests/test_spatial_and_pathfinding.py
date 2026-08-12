from __future__ import annotations

import pytest

from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.domain.services.pathfinding import PathfindingError, PathfindingService
from geo_mcp.domain.services.spatial_index import SpatialIndex


def _node(node_id: str, lat: float, lon: float, *, kind: str = "intersection") -> GraphNode:
    return GraphNode(id=node_id, point=GeoPoint(lat=lat, lon=lon), kind=kind, name=node_id)


def test_spatial_index_nearest() -> None:
    idx = SpatialIndex(cell_deg=0.01)
    nodes = {
        "a": _node("a", 0.0, 0.0),
        "b": _node("b", 0.0, 0.02),
        "c": _node("c", 1.0, 1.0),
    }
    idx.rebuild(nodes)
    hit = idx.nearest(GeoPoint(0.0, 0.001))
    assert hit is not None
    assert hit[0].id == "a"


def test_network_merge_and_nearest_stops() -> None:
    g1 = NetworkGraph()
    g1.add_node(_node("n1", 0.0, 0.0))
    g1.add_node(_node("s1", 0.0, 0.001, kind="stop"))
    g1.add_edge(
        GraphEdge(
            source_id="n1",
            target_id="s1",
            mode=EdgeMode.TRANSFER,
            length_m=10,
            travel_time_s=5,
        )
    )
    g2 = NetworkGraph()
    g2.add_node(_node("n1", 0.0, 0.0))
    g2.add_node(_node("s2", 0.0, 0.002, kind="stop"))
    g1.merge_from(g2)
    assert g1.node_count == 3
    stops = g1.nearest_stops(GeoPoint(0.0, 0.0), limit=2)
    assert len(stops) == 2


def test_multi_source_path() -> None:
    g = NetworkGraph()
    for n in (
        _node("A", 0.0, 0.0, kind="stop"),
        _node("B", 0.0, 0.01, kind="stop"),
        _node("C", 0.0, 0.02, kind="stop"),
    ):
        g.add_node(n)
    for src, tgt, length in (("A", "B", 100), ("B", "C", 100), ("A", "C", 500)):
        g.add_edge(
            GraphEdge(
                source_id=src,
                target_id=tgt,
                mode=EdgeMode.TRANSIT,
                length_m=length,
                travel_time_s=length / 8,
                line_ref="X",
            )
        )
    service = PathfindingService()
    itin = service.find_path_between_sets(
        g,
        {"A": 0.0, "B": 50.0},
        {"C"},
        allowed_modes=[EdgeMode.TRANSIT],
    )
    assert itin.total_distance_m > 0
    assert "transit" in itin.modes_used


def test_stitch_itineraries() -> None:
    service = PathfindingService()
    g = NetworkGraph()
    g.add_node(_node("A", 0.0, 0.0))
    g.add_node(_node("B", 0.0, 0.001))
    g.add_edge(
        GraphEdge(
            source_id="A",
            target_id="B",
            mode=EdgeMode.WALK,
            length_m=100,
            travel_time_s=70,
        )
    )
    a = service.find_path(
        g, GeoPoint(0.0, 0.0), GeoPoint(0.0, 0.001), allowed_modes=[EdgeMode.WALK]
    )
    b = service.find_path(
        g, GeoPoint(0.0, 0.0), GeoPoint(0.0, 0.001), allowed_modes=[EdgeMode.WALK]
    )
    stitched = service.stitch(a, b)
    assert len(stitched.legs) == 2


def test_snap_limit() -> None:
    g = NetworkGraph()
    g.add_node(_node("A", 0.0, 0.0))
    service = PathfindingService()
    with pytest.raises(PathfindingError):
        service.find_path(
            g,
            GeoPoint(0.0, 0.0),
            GeoPoint(1.0, 1.0),
            max_snap_m=50,
        )
