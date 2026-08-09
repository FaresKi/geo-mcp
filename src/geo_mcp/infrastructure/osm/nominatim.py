from __future__ import annotations

import logging

import httpx

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.place import Place
from geo_mcp.infrastructure.config import Settings

logger = logging.getLogger(__name__)


class NominatimGeocoder:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.http_timeout_s,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def geocode(self, query: str, *, limit: int = 5) -> list[Place]:
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

    def reverse_geocode(self, point: GeoPoint) -> Place | None:
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
