from __future__ import annotations

import pytest

from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.domain.services.pathfinding import PathfindingError, PathfindingService


def _node(node_id: str, lat: float, lon: float, *, kind: str = "intersection", name: str | None = None) -> GraphNode:
    return GraphNode(id=node_id, point=GeoPoint(lat=lat, lon=lon), kind=kind, name=name or node_id)


def _edge(
    src: str,
    tgt: str,
    *,
    mode: EdgeMode = EdgeMode.WALK,
    length_m: float = 100.0,
    line_ref: str | None = None,
    name: str | None = None,
) -> GraphEdge:
    speed = 1.4 if mode != EdgeMode.TRANSIT else 8.0
    return GraphEdge(
        source_id=src,
        target_id=tgt,
        mode=mode,
        length_m=length_m,
        travel_time_s=length_m / speed,
        name=name,
        line_ref=line_ref,
    )


@pytest.fixture
def sample_graph() -> NetworkGraph:
    """
    Walk corridor A--B--C with a transit shortcut A_stop--C_stop linked by transfers.
    """
    g = NetworkGraph()
    for node in (
        _node("A", 0.0, 0.0, name="Origin corner"),
        _node("B", 0.0, 0.001, name="Mid block"),
        _node("C", 0.0, 0.002, name="Dest corner"),
        _node("A_stop", 0.0001, 0.0, kind="stop", name="Stop A"),
        _node("C_stop", 0.0001, 0.002, kind="stop", name="Stop C"),
    ):
        g.add_node(node)

    for edge in (
        _edge("A", "B", length_m=110, name="Main St"),
        _edge("B", "A", length_m=110, name="Main St"),
        _edge("B", "C", length_m=110, name="Main St"),
        _edge("C", "B", length_m=110, name="Main St"),
        _edge("A", "A_stop", mode=EdgeMode.TRANSFER, length_m=15),
        _edge("A_stop", "A", mode=EdgeMode.TRANSFER, length_m=15),
        _edge("C", "C_stop", mode=EdgeMode.TRANSFER, length_m=15),
        _edge("C_stop", "C", mode=EdgeMode.TRANSFER, length_m=15),
        _edge(
            "A_stop",
            "C_stop",
            mode=EdgeMode.TRANSIT,
            length_m=200,
            line_ref="12",
            name="Bus 12",
        ),
        _edge(
            "C_stop",
            "A_stop",
            mode=EdgeMode.TRANSIT,
            length_m=200,
            line_ref="12",
            name="Bus 12",
        ),
    ):
        g.add_edge(edge)
    return g


def test_walk_only_path(sample_graph: NetworkGraph) -> None:
    service = PathfindingService()
    itinerary = service.find_path(
        sample_graph,
        GeoPoint(0.0, 0.0),
        GeoPoint(0.0, 0.002),
        allowed_modes=[EdgeMode.WALK],
    )
    assert itinerary.total_distance_m > 0
    assert all(leg.mode == EdgeMode.WALK for leg in itinerary.legs)
    assert "walk" in itinerary.modes_used


def test_multimodal_prefers_transit_shortcut(sample_graph: NetworkGraph) -> None:
    service = PathfindingService(transfer_penalty_s=10.0)
    itinerary = service.find_path(
        sample_graph,
        GeoPoint(0.0, 0.0),
        GeoPoint(0.0, 0.002),
        allowed_modes=[EdgeMode.WALK, EdgeMode.TRANSIT, EdgeMode.TRANSFER],
    )
    assert EdgeMode.TRANSIT in {leg.mode for leg in itinerary.legs}
    assert "12" in {leg.line_ref for leg in itinerary.legs if leg.line_ref}
    assert itinerary.narrative


def test_no_path_raises(sample_graph: NetworkGraph) -> None:
    sample_graph.add_node(_node("Z", 1.0, 1.0, name="Islanded"))
    service = PathfindingService()
    with pytest.raises(PathfindingError):
        service.find_path(
            sample_graph,
            GeoPoint(0.0, 0.0),
            GeoPoint(1.0, 1.0),
            allowed_modes=[EdgeMode.WALK],
            max_snap_m=50.0,
        )


def test_haversine_distance() -> None:
    a = GeoPoint(48.8566, 2.3522)
    b = GeoPoint(48.8566, 2.3522)
    assert a.distance_meters(b) == pytest.approx(0.0, abs=0.01)
