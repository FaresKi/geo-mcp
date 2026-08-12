from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from geo_mcp.domain.model.geo import GeoPoint


class RoutingStrategy(StrEnum):
    LOCAL = "local"
    HIERARCHICAL = "hierarchical"


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    strategy: RoutingStrategy
    distance_m: float
    reason: str


class RoutingStrategySelector:
    """Choose local A* vs hierarchical transit-first routing by distance."""

    def __init__(self, *, local_max_m: float = 2_000.0) -> None:
        self.local_max_m = local_max_m

    def decide(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        modes: list[str] | None = None,
        force: RoutingStrategy | None = None,
    ) -> StrategyDecision:
        if force is not None:
            dist = origin.distance_meters(destination)
            return StrategyDecision(
                strategy=force,
                distance_m=dist,
                reason=f"forced:{force.value}",
            )
        dist = origin.distance_meters(destination)
        walk_only = bool(modes) and all(
            m.lower().strip() in {"walk", "walking", "foot"} for m in modes
        )
        if walk_only:
            return StrategyDecision(
                strategy=RoutingStrategy.LOCAL,
                distance_m=dist,
                reason="walk_only",
            )
        if dist <= self.local_max_m:
            return StrategyDecision(
                strategy=RoutingStrategy.LOCAL,
                distance_m=dist,
                reason=f"distance<={self.local_max_m:.0f}m",
            )
        return StrategyDecision(
            strategy=RoutingStrategy.HIERARCHICAL,
            distance_m=dist,
            reason=f"distance>{self.local_max_m:.0f}m",
        )
