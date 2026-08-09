from __future__ import annotations

from dataclasses import dataclass

from geo_mcp.application.dto import LoadAreaResult
from geo_mcp.application.mappers import bbox_to_dto
from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.ports import GeocoderPort
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.osm.network_repository import AreaSession, OsmNetworkRepository


@dataclass
class LoadAreaUseCase:
    geocoder: GeocoderPort
    networks: OsmNetworkRepository
    session: AreaSession
    settings: Settings

    def execute(
        self,
        *,
        place: str | None = None,
        south: float | None = None,
        west: float | None = None,
        north: float | None = None,
        east: float | None = None,
        force_refresh: bool = False,
    ) -> LoadAreaResult:
        label = place or "bbox"
        if place:
            results = self.geocoder.geocode(place, limit=1)
            if not results:
                raise ValueError(f"Could not geocode place: {place}")
            found = results[0]
            label = found.display_name
            if found.bbox is not None:
                bbox = found.bbox
                # Cap very large place bboxes for MVP responsiveness.
                lat_span = bbox.north - bbox.south
                lon_span = bbox.east - bbox.west
                if lat_span > 0.05 or lon_span > 0.05:
                    bbox = BoundingBox.from_center(
                        found.point, self.settings.default_area_half_size_deg
                    )
            else:
                bbox = BoundingBox.from_center(
                    found.point, self.settings.default_area_half_size_deg
                )
        else:
            if None in (south, west, north, east):
                raise ValueError("Provide place or full bbox (south, west, north, east)")
            bbox = BoundingBox(south=south, west=west, north=north, east=east)

        cached_before = self.networks.get_cached(bbox) is not None and not force_refresh
        graph = self.networks.load_area(bbox, force_refresh=force_refresh)
        self.session.graph = graph
        self.session.bbox = graph.bbox or bbox.rounded()
        self.session.label = label
        self.session.line_index.clear()
        for line in self.networks.current_lines:
            for stop_id in line.stop_ids:
                self.session.line_index[stop_id].append(line.ref)

        return LoadAreaResult(
            label=label,
            bbox=bbox_to_dto(self.session.bbox),
            node_count=graph.node_count,
            edge_count=graph.edge_count,
            stop_count=len(graph.stop_nodes()),
            major_roads=graph.major_roads,
            landmarks=graph.landmarks,
            cached=cached_before,
        )


@dataclass
class GeocodeUseCase:
    geocoder: GeocoderPort

    def execute(self, query: str, *, limit: int = 5):
        from geo_mcp.application.mappers import place_to_dto

        return [place_to_dto(p) for p in self.geocoder.geocode(query, limit=limit)]


@dataclass
class ReverseGeocodeUseCase:
    geocoder: GeocoderPort

    def execute(self, lat: float, lon: float):
        from geo_mcp.application.mappers import place_to_dto

        place = self.geocoder.reverse_geocode(GeoPoint(lat=lat, lon=lon))
        return place_to_dto(place) if place else None
