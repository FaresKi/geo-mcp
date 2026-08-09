from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt


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

    @classmethod
    def from_center(cls, center: GeoPoint, half_size_degrees: float) -> BoundingBox:
        return cls(
            south=center.lat - half_size_degrees,
            west=center.lon - half_size_degrees,
            north=center.lat + half_size_degrees,
            east=center.lon + half_size_degrees,
        )
