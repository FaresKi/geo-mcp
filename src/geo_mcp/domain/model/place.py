from __future__ import annotations

from dataclasses import dataclass, field

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint


@dataclass(frozen=True, slots=True)
class Place:
    display_name: str
    point: GeoPoint
    bbox: BoundingBox | None = None
    place_type: str | None = None
    osm_id: str | None = None
    address: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PointOfInterest:
    name: str
    point: GeoPoint
    category: str
    osm_id: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    distance_m: float | None = None
