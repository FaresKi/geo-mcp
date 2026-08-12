from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, floor, radians, sin, sqrt


@dataclass(frozen=True, slots=True)
class GeoPoint:
    lat: float
    lon: float

    def distance_meters(self, other: GeoPoint) -> float:
        """Haversine distance in meters."""
        r = 6_371_000.0
        lat1, lon1, lat2, lon2 = map(
            radians, (self.lat, self.lon, other.lat, other.lon)
        )
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return 2 * r * asin(sqrt(a))


@dataclass(frozen=True, slots=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float

    def contains(self, point: GeoPoint) -> bool:
        return (
            self.south <= point.lat <= self.north
            and self.west <= point.lon <= self.east
        )

    def expand(self, padding_degrees: float) -> BoundingBox:
        return BoundingBox(
            south=self.south - padding_degrees,
            west=self.west - padding_degrees,
            north=self.north + padding_degrees,
            east=self.east + padding_degrees,
        )

    def rounded(self, decimals: int = 3) -> BoundingBox:
        return BoundingBox(
            south=round(self.south, decimals),
            west=round(self.west, decimals),
            north=round(self.north, decimals),
            east=round(self.east, decimals),
        )

    def cache_key(self) -> str:
        b = self.rounded()
        return f"{b.south}_{b.west}_{b.north}_{b.east}"

    def spans(self) -> tuple[float, float]:
        return self.north - self.south, self.east - self.west

    @classmethod
    def from_center(cls, center: GeoPoint, half_size_degrees: float) -> BoundingBox:
        return cls(
            south=center.lat - half_size_degrees,
            west=center.lon - half_size_degrees,
            north=center.lat + half_size_degrees,
            east=center.lon + half_size_degrees,
        )

    @classmethod
    def from_points(cls, *points: GeoPoint, pad_deg: float = 0.0) -> BoundingBox:
        if not points:
            raise ValueError("Need at least one point")
        lats = [p.lat for p in points]
        lons = [p.lon for p in points]
        return cls(
            south=min(lats) - pad_deg,
            west=min(lons) - pad_deg,
            north=max(lats) + pad_deg,
            east=max(lons) + pad_deg,
        )


@dataclass(frozen=True, slots=True)
class TileId:
    """Fixed-degree grid tile covering a walk/transit graph shard."""

    lat_idx: int
    lon_idx: int
    size_deg: float = 0.01

    @classmethod
    def from_point(cls, point: GeoPoint, size_deg: float = 0.01) -> TileId:
        return cls(
            lat_idx=floor(point.lat / size_deg),
            lon_idx=floor(point.lon / size_deg),
            size_deg=size_deg,
        )

    @classmethod
    def covering(
        cls,
        bbox: BoundingBox,
        size_deg: float = 0.01,
        *,
        max_tiles: int | None = None,
    ) -> list[TileId]:
        south_i = floor(bbox.south / size_deg)
        north_i = floor((bbox.north - 1e-12) / size_deg)
        west_i = floor(bbox.west / size_deg)
        east_i = floor((bbox.east - 1e-12) / size_deg)
        tiles = [
            cls(lat_idx=lat_i, lon_idx=lon_i, size_deg=size_deg)
            for lat_i in range(south_i, north_i + 1)
            for lon_i in range(west_i, east_i + 1)
        ]
        if max_tiles is not None and len(tiles) > max_tiles:
            # Prefer tiles closest to bbox center when over budget.
            center = GeoPoint(
                lat=(bbox.south + bbox.north) / 2,
                lon=(bbox.west + bbox.east) / 2,
            )
            tiles.sort(key=lambda t: t.center().distance_meters(center))
            tiles = tiles[:max_tiles]
        return tiles

    def bbox(self) -> BoundingBox:
        return BoundingBox(
            south=self.lat_idx * self.size_deg,
            west=self.lon_idx * self.size_deg,
            north=(self.lat_idx + 1) * self.size_deg,
            east=(self.lon_idx + 1) * self.size_deg,
        )

    def center(self) -> GeoPoint:
        b = self.bbox()
        return GeoPoint(
            lat=(b.south + b.north) / 2,
            lon=(b.west + b.east) / 2,
        )

    def cache_key(self, layer: str = "walk") -> str:
        return f"{layer}:{self.size_deg}:{self.lat_idx}:{self.lon_idx}"

    def neighbors(self, ring: int = 1) -> list[TileId]:
        tiles: list[TileId] = []
        for dlat in range(-ring, ring + 1):
            for dlon in range(-ring, ring + 1):
                tiles.append(
                    TileId(
                        lat_idx=self.lat_idx + dlat,
                        lon_idx=self.lon_idx + dlon,
                        size_deg=self.size_deg,
                    )
                )
        return tiles
