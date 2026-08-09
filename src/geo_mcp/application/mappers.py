from __future__ import annotations

from geo_mcp.application.dto import (
    BoundingBoxDTO,
    GeoPointDTO,
    ItineraryDTO,
    LoadAreaResult,
    PlaceDTO,
    PoiDTO,
    RouteLegDTO,
    SnapResultDTO,
    TransitStopDTO,
)
from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.place import Place
from geo_mcp.domain.model.route import Itinerary


def point_to_dto(point: GeoPoint) -> GeoPointDTO:
    return GeoPointDTO(lat=point.lat, lon=point.lon)


def bbox_to_dto(bbox: BoundingBox) -> BoundingBoxDTO:
    return BoundingBoxDTO(
        south=bbox.south, west=bbox.west, north=bbox.north, east=bbox.east
    )


def place_to_dto(place: Place) -> PlaceDTO:
    return PlaceDTO(
        display_name=place.display_name,
        point=point_to_dto(place.point),
        bbox=bbox_to_dto(place.bbox) if place.bbox else None,
        place_type=place.place_type,
        osm_id=place.osm_id,
        address=place.address,
    )


def itinerary_to_dto(itinerary: Itinerary) -> ItineraryDTO:
    return ItineraryDTO(
        total_distance_m=itinerary.total_distance_m,
        total_duration_s=itinerary.total_duration_s,
        transfer_count=itinerary.transfer_count,
        narrative=itinerary.narrative,
        modes_used=list(itinerary.modes_used),
        legs=[
            RouteLegDTO(
                mode=leg.mode.value,
                distance_m=leg.distance_m,
                duration_s=leg.duration_s,
                from_name=leg.from_name,
                to_name=leg.to_name,
                street_name=leg.street_name,
                line_ref=leg.line_ref,
                instruction=leg.instruction,
                geometry=[point_to_dto(p) for p in leg.geometry],
            )
            for leg in itinerary.legs
        ],
    )


__all__ = [
    "BoundingBoxDTO",
    "GeoPointDTO",
    "ItineraryDTO",
    "LoadAreaResult",
    "PlaceDTO",
    "PoiDTO",
    "RouteLegDTO",
    "SnapResultDTO",
    "TransitStopDTO",
    "bbox_to_dto",
    "itinerary_to_dto",
    "place_to_dto",
    "point_to_dto",
]
