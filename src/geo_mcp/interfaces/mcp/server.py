from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from geo_mcp.container import AppContainer, build_container

logger = logging.getLogger(__name__)


def create_mcp_server(
    container: AppContainer | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    container = container or build_container()
    mcp = FastMCP(
        "geo-mcp",
        instructions=(
            "OSM-backed multimodal (walk + transit) navigation tools with "
            "distance-adaptive routing. Prefer geocode for coordinates. "
            "plan_route auto-loads tiles and works for short or long trips; "
            "long routes may return a job_id — poll get_job until completed. "
            "load_area is optional prefetch for explore/describe_area."
        ),
        host=host,
        port=port,
    )

    def _progress_callback(ctx: Context):
        loop = asyncio.get_running_loop()

        def progress(p: float, total: float | None, message: str) -> None:
            async def _send() -> None:
                try:
                    await ctx.report_progress(
                        progress=p, total=total, message=message
                    )
                except Exception:
                    logger.debug("progress notification failed", exc_info=True)

            try:
                fut = asyncio.run_coroutine_threadsafe(_send(), loop)
                fut.result(timeout=2.0)
            except Exception:
                logger.debug("progress schedule failed", exc_info=True)

        return progress

    @mcp.tool()
    async def load_area(
        place: str | None = None,
        south: float | None = None,
        west: float | None = None,
        north: float | None = None,
        east: float | None = None,
        force_refresh: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Load and cache OSM walk+transit graph for a place name or bounding box.

        Large bboxes are fetched as parallel tiles. Prefer a place query for explore.
        Optional before plan_route; useful for describe_area.
        """
        import anyio

        on_progress = _progress_callback(ctx) if ctx is not None else None
        result = await anyio.to_thread.run_sync(
            lambda: container.load_area.execute(
                place=place,
                south=south,
                west=west,
                north=north,
                east=east,
                force_refresh=force_refresh,
                on_progress=on_progress,
            )
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
    async def plan_route(
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        modes: list[str] | None = None,
        defer: bool = False,
        force_strategy: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Plan a multimodal route for any distance.

        Auto-selects local A* or hierarchical transit-first routing.
        Long trips may return {job_id, status} — use get_job to poll.
        Set defer=true to always run in the background.
        force_strategy: "local" | "hierarchical".
        """
        import anyio

        on_progress = _progress_callback(ctx) if ctx is not None else None
        result = await anyio.to_thread.run_sync(
            lambda: container.plan_route.execute(
                origin_lat=origin_lat,
                origin_lon=origin_lon,
                destination_lat=destination_lat,
                destination_lon=destination_lon,
                modes=modes,
                defer=defer,
                force_strategy=force_strategy,
                on_progress=on_progress,
            )
        )
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result  # job dict

    @mcp.tool()
    def submit_route(
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        modes: list[str] | None = None,
        force_strategy: str | None = None,
    ) -> dict[str, Any]:
        """Submit a route plan as a background job. Poll with get_job."""
        return container.plan_route.submit(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            destination_lat=destination_lat,
            destination_lon=destination_lon,
            modes=modes,
            force_strategy=force_strategy,
        )

    @mcp.tool()
    def get_job(job_id: str) -> dict[str, Any]:
        """Get status/result of a deferred job (plan_route / submit_route)."""
        job = container.jobs.get(job_id)
        if job is None:
            raise ValueError(f"Unknown job_id: {job_id}")
        return job.to_dict()

    @mcp.tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Request cancellation of a queued/running job."""
        job = container.jobs.cancel(job_id)
        if job is None:
            raise ValueError(f"Unknown job_id: {job_id}")
        return job.to_dict()

    @mcp.tool()
    def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
        """List recent background jobs."""
        return [j.to_dict() for j in container.jobs.list_jobs(limit=limit)]

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
        """Snap a coordinate to the nearest graph node (auto-loads a local tile if needed)."""
        return container.snap_to_network.execute(lat=lat, lon=lon, kind=kind).model_dump()

    return mcp
