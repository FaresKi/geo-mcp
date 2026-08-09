from __future__ import annotations

import logging
from typing import Any

import httpx

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.place import PointOfInterest
from geo_mcp.domain.model.transit import TransitLine, TransitStop
from geo_mcp.infrastructure.config import Settings

logger = logging.getLogger(__name__)

CATEGORY_TAG_MAP: dict[str, str] = {
    "cafe": '["amenity"="cafe"]',
    "restaurant": '["amenity"="restaurant"]',
    "pharmacy": '["amenity"="pharmacy"]',
    "hospital": '["amenity"="hospital"]',
    "atm": '["amenity"="atm"]',
    "bank": '["amenity"="bank"]',
    "supermarket": '["shop"="supermarket"]',
    "convenience": '["shop"="convenience"]',
    "park": '["leisure"="park"]',
    "museum": '["tourism"="museum"]',
    "hotel": '["tourism"="hotel"]',
    "school": '["amenity"="school"]',
    "toilet": '["amenity"="toilets"]',
    "landmark": '["tourism"~"attraction|viewpoint|museum"]',
}


class OverpassClient:
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

    def _endpoints(self) -> list[str]:
        mirrors = [
            u.strip()
            for u in self._settings.overpass_mirrors.split(",")
            if u.strip()
        ]
        ordered = [self._settings.overpass_url, *mirrors]
        # Preserve order, unique.
        seen: set[str] = set()
        result: list[str] = []
        for url in ordered:
            if url not in seen:
                seen.add(url)
                result.append(url)
        return result

    def _query(self, ql: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for url in self._endpoints():
            for attempt in range(self._settings.overpass_retries + 1):
                try:
                    response = self._client.post(url, data={"data": ql})
                    response.raise_for_status()
                    return response.json()
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Overpass query failed (%s attempt %s): %s",
                        url,
                        attempt + 1,
                        exc,
                    )
        assert last_error is not None
        raise last_error

    def fetch_transit(self, bbox: BoundingBox) -> tuple[list[TransitStop], list[TransitLine]]:
        b = f"{bbox.south},{bbox.west},{bbox.north},{bbox.east}"
        # Lean query: stops first; route relations without full geometry recurse.
        ql = f"""
        [out:json][timeout:60];
        (
          node["highway"="bus_stop"]({b});
          node["public_transport"="platform"]({b});
          node["public_transport"="stop_position"]({b});
          node["railway"="station"]({b});
          node["railway"="halt"]({b});
          node["railway"="tram_stop"]({b});
          node["amenity"="ferry_terminal"]({b});
          node["amenity"="bus_station"]({b});
          relation["type"="route"]["route"~"bus|tram|subway|train|light_rail|ferry"]({b});
        );
        out body;
        """
        data = self._query(ql)
        elements = data.get("elements", [])

        stops: dict[str, TransitStop] = {}
        lines: list[TransitLine] = []
        nodes_by_id: dict[int, dict[str, Any]] = {
            el["id"]: el for el in elements if el.get("type") == "node"
        }

        for el in elements:
            if el.get("type") != "node":
                continue
            tags = el.get("tags") or {}
            is_stop = any(
                [
                    tags.get("highway") == "bus_stop",
                    tags.get("public_transport") in {"platform", "stop_position"},
                    tags.get("railway") in {"station", "halt", "tram_stop"},
                    tags.get("amenity") in {"bus_station", "ferry_terminal"},
                ]
            )
            if not is_stop:
                continue
            stop_id = f"stop:{el['id']}"
            modes = []
            if tags.get("highway") == "bus_stop" or tags.get("amenity") == "bus_station":
                modes.append("bus")
            if tags.get("amenity") == "ferry_terminal":
                modes.append("ferry")
            if tags.get("railway") in {"station", "halt"}:
                modes.append("rail")
            if tags.get("railway") == "tram_stop":
                modes.append("tram")
            if tags.get("public_transport"):
                modes.append(tags["public_transport"])
            stops[stop_id] = TransitStop(
                id=stop_id,
                name=tags.get("name") or tags.get("ref") or stop_id,
                point=GeoPoint(lat=float(el["lat"]), lon=float(el["lon"])),
                modes=tuple(dict.fromkeys(modes)) or ("transit",),
                osm_id=str(el["id"]),
            )

        for el in elements:
            if el.get("type") != "relation":
                continue
            tags = el.get("tags") or {}
            route_mode = tags.get("route", "bus")
            ref = tags.get("ref") or tags.get("name") or f"route:{el['id']}"
            stop_ids: list[str] = []
            for member in el.get("members") or []:
                if member.get("type") != "node":
                    continue
                role = member.get("role") or ""
                if role and role not in {"stop", "platform", "stop_entry_only", "stop_exit_only"}:
                    continue
                nid = member.get("ref")
                sid = f"stop:{nid}"
                if sid not in stops and nid in nodes_by_id:
                    node = nodes_by_id[nid]
                    if "lat" not in node:
                        continue
                    ntags = node.get("tags") or {}
                    stops[sid] = TransitStop(
                        id=sid,
                        name=ntags.get("name") or ntags.get("ref") or sid,
                        point=GeoPoint(lat=float(node["lat"]), lon=float(node["lon"])),
                        modes=(route_mode,),
                        osm_id=str(nid),
                    )
                if sid in stops:
                    stop_ids.append(sid)
                    existing = stops[sid]
                    lines_set = set(existing.lines) | {ref}
                    modes_set = set(existing.modes) | {route_mode}
                    stops[sid] = TransitStop(
                        id=existing.id,
                        name=existing.name,
                        point=existing.point,
                        modes=tuple(modes_set),
                        lines=tuple(sorted(lines_set)),
                        osm_id=existing.osm_id,
                    )
            if stop_ids:
                lines.append(
                    TransitLine(
                        ref=ref,
                        name=tags.get("name"),
                        mode=route_mode,
                        stop_ids=tuple(dict.fromkeys(stop_ids)),
                        tags={k: str(v) for k, v in tags.items()},
                    )
                )

        return list(stops.values()), lines

    def find_nearby(
        self,
        point: GeoPoint,
        *,
        radius_m: float = 400.0,
        category: str | None = None,
        limit: int = 20,
    ) -> list[PointOfInterest]:
        if category and category in CATEGORY_TAG_MAP:
            filters = [CATEGORY_TAG_MAP[category]]
        elif category:
            # Treat free-form category as amenity tag.
            filters = [f'["amenity"="{category}"]']
        else:
            filters = [
                '["amenity"]',
                '["shop"]',
                '["tourism"]',
                '["leisure"="park"]',
            ]

        around = f"(around:{int(radius_m)},{point.lat},{point.lon})"
        parts = []
        for filt in filters:
            parts.append(f"node{filt}{around};")
            parts.append(f"way{filt}{around};")
        ql = f"""
        [out:json][timeout:45];
        (
          {"".join(parts)}
        );
        out center tags {limit};
        """
        data = self._query(ql)
        results: list[PointOfInterest] = []
        for el in data.get("elements", []):
            tags = el.get("tags") or {}
            name = tags.get("name")
            if not name:
                continue
            if "lat" in el and "lon" in el:
                poi_point = GeoPoint(lat=float(el["lat"]), lon=float(el["lon"]))
            elif "center" in el:
                poi_point = GeoPoint(
                    lat=float(el["center"]["lat"]),
                    lon=float(el["center"]["lon"]),
                )
            else:
                continue
            cat = (
                tags.get("amenity")
                or tags.get("shop")
                or tags.get("tourism")
                or tags.get("leisure")
                or "poi"
            )
            results.append(
                PointOfInterest(
                    name=name,
                    point=poi_point,
                    category=cat,
                    osm_id=f"{el.get('type')}/{el.get('id')}",
                    tags={k: str(v) for k, v in tags.items()},
                    distance_m=point.distance_meters(poi_point),
                )
            )
        results.sort(key=lambda p: p.distance_m or 0)
        return results[:limit]

    def describe_features(self, bbox: BoundingBox) -> dict[str, list[str]]:
        b = f"{bbox.south},{bbox.west},{bbox.north},{bbox.east}"
        ql = f"""
        [out:json][timeout:45];
        (
          way["highway"~"primary|secondary|tertiary|trunk|motorway"]["name"]({b});
          relation["boundary"="administrative"]["admin_level"~"8|9|10"]["name"]({b});
          node["tourism"~"attraction|museum|viewpoint"]["name"]({b});
          node["historic"]["name"]({b});
        );
        out tags 80;
        """
        data = self._query(ql)
        roads: list[str] = []
        neighborhoods: list[str] = []
        landmarks: list[str] = []
        for el in data.get("elements", []):
            tags = el.get("tags") or {}
            name = tags.get("name")
            if not name:
                continue
            if tags.get("highway"):
                roads.append(name)
            elif tags.get("boundary") == "administrative":
                neighborhoods.append(name)
            else:
                landmarks.append(name)
        return {
            "major_roads": list(dict.fromkeys(roads))[:20],
            "neighborhoods": list(dict.fromkeys(neighborhoods))[:20],
            "landmarks": list(dict.fromkeys(landmarks))[:20],
        }
