from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from geo_mcp.application.dto import LoadAreaResult
from geo_mcp.application.mappers import bbox_to_dto
from geo_mcp.application.session import AreaSession
from geo_mcp.domain.config import RoutingConfig
from geo_mcp.domain.deadline import Deadline
from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.ports import GeocoderPort, NetworkRepositoryPort, TileGraphPort


@dataclass
class LoadAreaUseCase:
    geocoder: GeocoderPort
    networks: NetworkRepositoryPort
    tiles: TileGraphPort
    session: AreaSession
    config: RoutingConfig

    def execute(
        self,
        *,
        place: str | None = None,
        south: float | None = None,
        west: float | None = None,
        north: float | None = None,
        east: float | None = None,
        force_refresh: bool = False,
        on_progress: Callable[[float, float | None, str], None] | None = None,
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
                lat_span, lon_span = bbox.spans()
                max_span = self.config.place_bbox_max_span_deg
                if lat_span > max_span or lon_span > max_span:
                    bbox = BoundingBox.from_center(
                        found.point, self.config.default_area_half_size_deg
                    )
            else:
                bbox = BoundingBox.from_center(
                    found.point, self.config.default_area_half_size_deg
                )
        else:
            if None in (south, west, north, east):
                raise ValueError("Provide place or full bbox (south, west, north, east)")
            bbox = BoundingBox(south=south, west=west, north=north, east=east)

        if on_progress:
            on_progress(5, 100, f"Resolving area {label}")

        lat_span, lon_span = bbox.spans()
        use_tiles = (
            lat_span > self.config.tile_size_deg * 1.5
            or lon_span > self.config.tile_size_deg * 1.5
        )
        cached_before = self.networks.get_cached(bbox) is not None and not force_refresh

        if use_tiles:
            tile_ids = self.tiles.tiles_for_bbox(
                bbox, max_tiles=max(self.config.max_walk_tiles, 25)
            )
            if on_progress:
                on_progress(10, 100, f"Loading {len(tile_ids)} tiles")
            graph = self.tiles.load_tiles(
                tile_ids,
                layer="full",
                force_refresh=force_refresh,
                deadline=Deadline.from_seconds(self.config.operation_deadline_s),
                on_progress=lambda d, t, m: on_progress(
                    10 + 80 * (d / max(t, 1)), 100, m
                )
                if on_progress
                else None,
            )
            cached_before = False
        else:
            if on_progress:
                on_progress(20, 100, "Loading network graph")
            graph = self.networks.load_area(bbox, force_refresh=force_refresh)

        self.session.graph = graph
        self.session.bbox = graph.bbox or bbox.rounded()
        self.session.label = label
        self.session.line_index.clear()
        for line in self.networks.current_lines:
            for stop_id in line.stop_ids:
                self.session.line_index[stop_id].append(line.ref)

        if on_progress:
            on_progress(100, 100, "Area ready")

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
