from __future__ import annotations

from typing import Protocol

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.network import NetworkGraph
from geo_mcp.domain.model.place import Place, PointOfInterest
from geo_mcp.domain.model.transit import TransitLine, TransitStop


class GeocoderPort(Protocol):
    def geocode(self, query: str, *, limit: int = 5) -> list[Place]:
        ...

    def reverse_geocode(self, point: GeoPoint) -> Place | None:
        ...


class NetworkRepositoryPort(Protocol):
    def load_area(
        self,
        bbox: BoundingBox,
        *,
        force_refresh: bool = False,
    ) -> NetworkGraph:
        ...

    def get_cached(self, bbox: BoundingBox) -> NetworkGraph | None:
        ...


class PoiSearchPort(Protocol):
    def find_nearby(
        self,
        point: GeoPoint,
        *,
        radius_m: float = 400.0,
        category: str | None = None,
        limit: int = 20,
    ) -> list[PointOfInterest]:
        ...


class TransitRepositoryPort(Protocol):
    def list_stops_nearby(
        self,
        point: GeoPoint,
        *,
        radius_m: float = 500.0,
        limit: int = 20,
    ) -> list[TransitStop]:
        ...

    def list_lines(self, bbox: BoundingBox) -> list[TransitLine]:
        ...
