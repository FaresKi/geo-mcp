from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from geo_mcp.domain.deadline import Deadline
from geo_mcp.domain.model.geo import BoundingBox, GeoPoint, TileId
from geo_mcp.domain.model.network import NetworkGraph
from geo_mcp.domain.model.place import Place, PointOfInterest
from geo_mcp.domain.model.transit import TransitLine, TransitStop

TileLayer = Literal["walk", "transit", "full"]
ProgressCallback = Callable[[int, int, str], None]


class GeocoderPort(Protocol):
    def geocode(self, query: str, *, limit: int = 5) -> list[Place]:
        ...

    def reverse_geocode(self, point: GeoPoint) -> Place | None:
        ...


class NetworkRepositoryPort(Protocol):
    current_lines: list[TransitLine]

    def load_area(
        self,
        bbox: BoundingBox,
        *,
        force_refresh: bool = False,
    ) -> NetworkGraph:
        ...

    def get_cached(self, bbox: BoundingBox) -> NetworkGraph | None:
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


class FeatureDescribePort(Protocol):
    def describe_features(self, bbox: BoundingBox) -> dict[str, list[str]]:
        ...


class TileGraphPort(Protocol):
    def tiles_for_point(self, point: GeoPoint, *, ring: int = 0) -> list[TileId]:
        ...

    def tiles_for_bbox(
        self,
        bbox: BoundingBox,
        *,
        max_tiles: int | None = None,
    ) -> list[TileId]:
        ...

    def load_tiles(
        self,
        tiles: list[TileId],
        *,
        layer: TileLayer = "full",
        force_refresh: bool = False,
        deadline: Deadline | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> NetworkGraph:
        ...

    def load_walk_around(
        self,
        point: GeoPoint,
        *,
        ring: int = 1,
        deadline: Deadline | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> NetworkGraph:
        ...

    def load_transit_skeleton(
        self,
        bbox: BoundingBox,
        *,
        deadline: Deadline | None = None,
    ) -> tuple[NetworkGraph, list[TransitStop], list[TransitLine]]:
        ...
