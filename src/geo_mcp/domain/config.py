from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    """Application/domain routing knobs — plain data, no env/framework coupling."""

    walk_speed_mps: float = 1.4
    local_max_m: float = 2_000.0
    max_snap_m: float = 400.0
    access_radius_m: float = 800.0
    tile_size_deg: float = 0.01
    max_walk_tiles: int = 9
    transit_bbox_pad_deg: float = 0.06
    transit_min_span_deg: float = 0.08
    prefer_rail_access: bool = True
    operation_deadline_s: float = 120.0
    place_bbox_max_span_deg: float = 0.05
    default_area_half_size_deg: float = 0.01
