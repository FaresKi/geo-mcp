from __future__ import annotations

from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.services.routing_strategy import (
    RoutingStrategy,
    RoutingStrategySelector,
)


def test_local_for_short_distance() -> None:
    selector = RoutingStrategySelector(local_max_m=2000)
    d = selector.decide(GeoPoint(48.85, 2.35), GeoPoint(48.851, 2.351))
    assert d.strategy == RoutingStrategy.LOCAL


def test_hierarchical_for_long_distance() -> None:
    selector = RoutingStrategySelector(local_max_m=2000)
    # Antony-ish to Pont Cardinet-ish
    d = selector.decide(GeoPoint(48.755, 2.301), GeoPoint(48.888, 2.314))
    assert d.strategy == RoutingStrategy.HIERARCHICAL
    assert d.distance_m > 10_000


def test_walk_only_forces_local() -> None:
    selector = RoutingStrategySelector(local_max_m=500)
    d = selector.decide(
        GeoPoint(48.755, 2.301),
        GeoPoint(48.888, 2.314),
        modes=["walk"],
    )
    assert d.strategy == RoutingStrategy.LOCAL
    assert d.reason == "walk_only"


def test_force_strategy() -> None:
    selector = RoutingStrategySelector()
    d = selector.decide(
        GeoPoint(0, 0),
        GeoPoint(0, 0.001),
        force=RoutingStrategy.HIERARCHICAL,
    )
    assert d.strategy == RoutingStrategy.HIERARCHICAL
