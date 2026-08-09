from __future__ import annotations

from dataclasses import dataclass, field

from geo_mcp.domain.model.geo import GeoPoint


@dataclass(frozen=True, slots=True)
class TransitStop:
    id: str
    name: str
    point: GeoPoint
    modes: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    osm_id: str | None = None


@dataclass(frozen=True, slots=True)
class TransitLine:
    ref: str
    name: str | None = None
    mode: str = "bus"
    stop_ids: tuple[str, ...] = ()
    tags: dict[str, str] = field(default_factory=dict)
