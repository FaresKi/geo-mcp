#!/usr/bin/env python3
"""Live smoke: load a small bbox, plan a short walk route."""

from __future__ import annotations

import sys

from geo_mcp.container import build_container


def main() -> int:
    # Small rectangle around Notre-Dame / Île de la Cité, Paris
    south, west, north, east = 48.8515, 2.3440, 48.8555, 2.3520
    container = build_container()
    try:
        loaded = container.load_area.execute(
            south=south,
            west=west,
            north=north,
            east=east,
            force_refresh=False,
        )
        print("loaded:", loaded.model_dump())
        route = container.plan_route.execute(
            origin_lat=48.8530,
            origin_lon=2.3460,
            destination_lat=48.8545,
            destination_lon=2.3495,
            modes=["walk", "transit"],
        )
        print("route:", route.model_dump())
        if not route.legs:
            print("FAIL: expected route legs", file=sys.stderr)
            return 1
        print("OK")
        return 0
    finally:
        container.close()


if __name__ == "__main__":
    raise SystemExit(main())
