from __future__ import annotations

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint, TileId


def test_tile_from_point_and_bbox() -> None:
    tile = TileId.from_point(GeoPoint(48.755, 2.301), size_deg=0.01)
    assert tile.size_deg == 0.01
    b = tile.bbox()
    assert b.contains(GeoPoint(48.755, 2.301))
    assert "walk:" in tile.cache_key("walk")


def test_tiles_covering_respects_max() -> None:
    bbox = BoundingBox(south=48.74, west=2.28, north=48.90, east=2.33)
    tiles = TileId.covering(bbox, size_deg=0.01, max_tiles=5)
    assert len(tiles) == 5


def test_bbox_from_points() -> None:
    a = GeoPoint(48.75, 2.30)
    b = GeoPoint(48.88, 2.31)
    box = BoundingBox.from_points(a, b, pad_deg=0.01)
    assert box.south < 48.75
    assert box.north > 48.88
    lat_span, lon_span = box.spans()
    assert lat_span > 0.1
