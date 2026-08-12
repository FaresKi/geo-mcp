from __future__ import annotations

from dataclasses import dataclass

from geo_mcp.application.dto import AreaDescriptionDTO, PoiDTO, TransitStopDTO
from geo_mcp.application.mappers import bbox_to_dto, point_to_dto
from geo_mcp.application.session import AreaSession
from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.ports import FeatureDescribePort, PoiSearchPort, TransitRepositoryPort


@dataclass
class FindNearbyUseCase:
    poi_search: PoiSearchPort

    def execute(
        self,
        *,
        lat: float,
        lon: float,
        radius_m: float = 400.0,
        category: str | None = None,
        limit: int = 20,
    ) -> list[PoiDTO]:
        pois = self.poi_search.find_nearby(
            GeoPoint(lat=lat, lon=lon),
            radius_m=radius_m,
            category=category,
            limit=limit,
        )
        return [
            PoiDTO(
                name=p.name,
                category=p.category,
                point=point_to_dto(p.point),
                distance_m=p.distance_m,
                osm_id=p.osm_id,
            )
            for p in pois
        ]


@dataclass
class ListTransitOptionsUseCase:
    transit: TransitRepositoryPort
    session: AreaSession

    def execute(
        self,
        *,
        lat: float,
        lon: float,
        radius_m: float = 500.0,
        limit: int = 20,
    ) -> list[TransitStopDTO]:
        point = GeoPoint(lat=lat, lon=lon)
        stops = self.transit.list_stops_nearby(point, radius_m=radius_m, limit=limit)
        result: list[TransitStopDTO] = []
        for stop in stops:
            lines = list(stop.lines)
            if not lines:
                lines = self.session.line_index.get(stop.id, [])
            result.append(
                TransitStopDTO(
                    id=stop.id,
                    name=stop.name,
                    point=point_to_dto(stop.point),
                    modes=list(stop.modes),
                    lines=lines,
                    distance_m=point.distance_meters(stop.point),
                )
            )
        return result


@dataclass
class DescribeAreaUseCase:
    session: AreaSession
    features: FeatureDescribePort

    def execute(self) -> AreaDescriptionDTO:
        graph = self.session.graph
        bbox = self.session.bbox
        if graph is None or bbox is None:
            raise RuntimeError("No area loaded. Call load_area first.")

        features = {
            "major_roads": graph.major_roads,
            "neighborhoods": [],
            "landmarks": graph.landmarks,
        }
        if len(features["major_roads"]) < 3 or len(features["landmarks"]) < 2:
            try:
                features = self.features.describe_features(bbox)
            except Exception:
                pass

        stop_count = len(graph.stop_nodes())
        notes = (
            f"Walk network has {graph.node_count} nodes and {graph.edge_count} edges. "
            f"{stop_count} transit stops are linked with transfer edges. "
            "Use major roads as orientation axes and landmarks as reference points "
            "when describing directions to a human."
        )
        return AreaDescriptionDTO(
            label=self.session.label,
            bbox=bbox_to_dto(bbox),
            major_roads=features.get("major_roads", graph.major_roads),
            neighborhoods=features.get("neighborhoods", []),
            landmarks=features.get("landmarks", graph.landmarks),
            connectivity_notes=notes,
            node_count=graph.node_count,
            edge_count=graph.edge_count,
            stop_count=stop_count,
        )
