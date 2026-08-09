from __future__ import annotations

from dataclasses import dataclass, field

from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.network import EdgeMode


@dataclass(frozen=True, slots=True)
class RouteLeg:
    mode: EdgeMode
    distance_m: float
    duration_s: float
    from_name: str | None
    to_name: str | None
    street_name: str | None = None
    line_ref: str | None = None
    geometry: tuple[GeoPoint, ...] = ()
    instruction: str = ""


@dataclass(frozen=True, slots=True)
class Itinerary:
    legs: tuple[RouteLeg, ...]
    total_distance_m: float
    total_duration_s: float
    transfer_count: int
    narrative: str
    node_ids: tuple[str, ...] = ()
    modes_used: tuple[str, ...] = field(default_factory=tuple)
