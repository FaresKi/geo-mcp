from __future__ import annotations

from pydantic import BaseModel, Field


class GeoPointDTO(BaseModel):
    lat: float
    lon: float


class BoundingBoxDTO(BaseModel):
    south: float
    west: float
    north: float
    east: float


class PlaceDTO(BaseModel):
    display_name: str
    point: GeoPointDTO
    bbox: BoundingBoxDTO | None = None
    place_type: str | None = None
    osm_id: str | None = None
    address: dict[str, str] = Field(default_factory=dict)


class LoadAreaResult(BaseModel):
    label: str
    bbox: BoundingBoxDTO
    node_count: int
    edge_count: int
    stop_count: int
    major_roads: list[str] = Field(default_factory=list)
    landmarks: list[str] = Field(default_factory=list)
    cached: bool


class RouteLegDTO(BaseModel):
    mode: str
    distance_m: float
    duration_s: float
    from_name: str | None = None
    to_name: str | None = None
    street_name: str | None = None
    line_ref: str | None = None
    instruction: str
    geometry: list[GeoPointDTO] = Field(default_factory=list)


class ItineraryDTO(BaseModel):
    total_distance_m: float
    total_duration_s: float
    transfer_count: int
    narrative: str
    modes_used: list[str]
    legs: list[RouteLegDTO]


class PoiDTO(BaseModel):
    name: str
    category: str
    point: GeoPointDTO
    distance_m: float | None = None
    osm_id: str | None = None


class TransitStopDTO(BaseModel):
    id: str
    name: str
    point: GeoPointDTO
    modes: list[str]
    lines: list[str]
    distance_m: float | None = None


class AreaDescriptionDTO(BaseModel):
    label: str | None
    bbox: BoundingBoxDTO | None
    major_roads: list[str]
    neighborhoods: list[str]
    landmarks: list[str]
    connectivity_notes: str
    node_count: int
    edge_count: int
    stop_count: int


class SnapResultDTO(BaseModel):
    node_id: str
    kind: str
    name: str | None
    point: GeoPointDTO
    distance_m: float
