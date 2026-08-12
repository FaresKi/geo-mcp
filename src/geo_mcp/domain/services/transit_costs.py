from __future__ import annotations

# Heuristic commercial speeds (m/s) by OSM route=* tag.
# Used when building transit edge travel_time_s and for A* heuristics.
ROUTE_SPEED_MPS: dict[str, float] = {
    "train": 18.0,  # ~65 km/h RER / suburban rail
    "subway": 12.0,  # ~43 km/h metro
    "light_rail": 9.0,
    "tram": 7.0,
    "ferry": 6.5,
    "bus": 5.0,  # ~18 km/h urban bus
    "trolleybus": 5.0,
    "share_taxi": 5.0,
}


def speed_for_route_mode(
    route_mode: str | None,
    *,
    default_mps: float = 8.0,
    ferry_mps: float = 6.5,
) -> float:
    if not route_mode:
        return default_mps
    key = route_mode.lower().strip()
    if key == "ferry":
        return ferry_mps
    return ROUTE_SPEED_MPS.get(key, default_mps)


def is_rail_like(route_mode: str | None) -> bool:
    if not route_mode:
        return False
    return route_mode.lower().strip() in {
        "train",
        "subway",
        "light_rail",
        "tram",
    }


def stop_rail_bonus(modes_tag: str | None) -> bool:
    """True if stop modes suggest rail/metro boarding preference."""
    if not modes_tag:
        return False
    parts = {p.strip().lower() for p in modes_tag.split(",") if p.strip()}
    return bool(
        parts
        & {
            "rail",
            "train",
            "subway",
            "light_rail",
            "tram",
            "station",
            "halt",
        }
    )
