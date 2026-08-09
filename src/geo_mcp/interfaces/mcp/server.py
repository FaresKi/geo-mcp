from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from geo_mcp.container import AppContainer, build_container

logger = logging.getLogger(__name__)


def create_mcp_server(container: AppContainer | None = None) -> FastMCP:
    container = container or build_container()
    mcp = FastMCP(
        "geo-mcp",
        instructions=(
            "OSM-backed multimodal (walk + transit) navigation tools. "
            "Always call load_area for the city/neighborhood before routing or "
            "describe_area. Prefer structured coordinates from geocode over guessing."
        ),
    )

    @mcp.tool()
    def load_area(
        place: str | None = None,
        south: float | None = None,
        west: float | None = None,
        north: float | None = None,
        east: float | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Load and cache OSM walk+transit graph for a place name or bounding box.

        Prefer a place query (e.g. "Le Marais, Paris") or an explicit bbox.
        Must be called before plan_route, describe_area, or snap_to_network.
        """
        result = container.load_area.execute(
            place=place,
            south=south,
            west=west,
            north=north,
            east=east,
            force_refresh=force_refresh,
        )
        return result.model_dump()

    @mcp.tool()
    def geocode(query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Resolve a place name or address to latitude/longitude and metadata."""
        return [p.model_dump() for p in container.geocode.execute(query, limit=limit)]

    @mcp.tool()
    def reverse_geocode(lat: float, lon: float) -> dict[str, Any] | None:
        """Resolve coordinates to a human-readable place."""
        place = container.reverse_geocode.execute(lat, lon)
        return place.model_dump() if place else None

    @mcp.tool()
    def plan_route(
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        modes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Plan a multimodal route on the loaded area graph.

        modes defaults to walk+transit. Use ["walk"] for pedestrian-only.
        Requires a prior successful load_area call.
        """
        result = container.plan_route.execute(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            destination_lat=destination_lat,
            destination_lon=destination_lon,
            modes=modes,
        )
        return result.model_dump()

    @mcp.tool()
    def find_nearby(
        lat: float,
        lon: float,
        radius_m: float = 400.0,
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Find nearby points of interest (cafes, pharmacies, parks, etc.)."""
        return [
            p.model_dump()
            for p in container.find_nearby.execute(
                lat=lat,
                lon=lon,
                radius_m=radius_m,
                category=category,
                limit=limit,
            )
        ]

    @mcp.tool()
    def list_transit_options(
        lat: float,
        lon: float,
        radius_m: float = 500.0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List nearby transit stops and the lines that serve them."""
        return [
            s.model_dump()
            for s in container.list_transit_options.execute(
                lat=lat, lon=lon, radius_m=radius_m, limit=limit
            )
        ]

    @mcp.tool()
    def describe_area() -> dict[str, Any]:
        """Describe the loaded area's structure: axes, neighborhoods, landmarks.

        Useful for giving LLMs city-design context before narrating directions.
        """
        return container.describe_area.execute().model_dump()

    @mcp.tool()
    def snap_to_network(
        lat: float,
        lon: float,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Snap a coordinate to the nearest loaded graph node (intersection or stop)."""
        return container.snap_to_network.execute(lat=lat, lon=lon, kind=kind).model_dump()

    return mcp
