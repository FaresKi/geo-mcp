from geo_mcp.application.dto import GeoPointDTO, PlaceDTO
from geo_mcp.application.use_cases.load_and_geocode import GeocodeUseCase
from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.place import Place


class FakeGeocoder:
    def geocode(self, query: str, *, limit: int = 5) -> list[Place]:
        return [
            Place(
                display_name=f"Result for {query}",
                point=GeoPoint(lat=48.85, lon=2.35),
            )
        ]

    def reverse_geocode(self, point: GeoPoint) -> Place | None:
        return Place(display_name="Somewhere", point=point)


def test_geocode_use_case() -> None:
    uc = GeocodeUseCase(FakeGeocoder())
    results = uc.execute("Paris")
    assert len(results) == 1
    assert isinstance(results[0], PlaceDTO)
    assert results[0].point == GeoPointDTO(lat=48.85, lon=2.35)
