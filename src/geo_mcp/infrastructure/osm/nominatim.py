from __future__ import annotations

import logging
import time

import httpx

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.place import Place
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.resilience import RetryPolicy, retry_call

logger = logging.getLogger(__name__)


class NominatimGeocoder:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.http_timeout_s,
        )
        self._owns_client = client is None
        self._retry = RetryPolicy(
            max_attempts=settings.nominatim_retries + 1,
            base_delay_s=settings.retry_base_delay_s,
            max_delay_s=settings.retry_max_delay_s,
        )
        self._last_request_at = 0.0
        self._min_interval_s = 1.0  # Nominatim usage policy

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)
        self._last_request_at = time.monotonic()

    def geocode(self, query: str, *, limit: int = 5) -> list[Place]:
        def _once() -> list[Place]:
            self._throttle()
            response = self._client.get(
                f"{self._settings.nominatim_url}/search",
                params={
                    "q": query,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": limit,
                },
            )
            response.raise_for_status()
            return [self._to_place(item) for item in response.json()]

        return retry_call(_once, policy=self._retry, label="nominatim.geocode")

    def reverse_geocode(self, point: GeoPoint) -> Place | None:
        def _once() -> Place | None:
            self._throttle()
            response = self._client.get(
                f"{self._settings.nominatim_url}/reverse",
                params={
                    "lat": point.lat,
                    "lon": point.lon,
                    "format": "jsonv2",
                    "addressdetails": 1,
                },
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                return None
            return self._to_place(data)

        return retry_call(_once, policy=self._retry, label="nominatim.reverse")

    def _to_place(self, item: dict) -> Place:
        bbox = None
        if "boundingbox" in item and len(item["boundingbox"]) == 4:
            south, north, west, east = (float(v) for v in item["boundingbox"])
            bbox = BoundingBox(south=south, west=west, north=north, east=east)
        address = {
            str(k): str(v)
            for k, v in (item.get("address") or {}).items()
            if isinstance(v, (str, int, float))
        }
        osm_type = item.get("osm_type")
        osm_id = item.get("osm_id")
        return Place(
            display_name=item.get("display_name") or item.get("name") or "Unknown",
            point=GeoPoint(lat=float(item["lat"]), lon=float(item["lon"])),
            bbox=bbox,
            place_type=item.get("type") or item.get("addresstype"),
            osm_id=f"{osm_type}/{osm_id}" if osm_type and osm_id else None,
            address=address,
        )
