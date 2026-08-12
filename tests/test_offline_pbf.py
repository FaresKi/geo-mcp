from __future__ import annotations

from pathlib import Path

import pytest

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.services.pathfinding import PathfindingService
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.graph.cache import DiskGraphCache
from geo_mcp.infrastructure.osm.network_repository import OsmNetworkRepository
from geo_mcp.infrastructure.osm.overpass import OverpassClient
from geo_mcp.infrastructure.osm.pbf_builder import (
    PbfNetworkBuilder,
    parse_transit_osm_xml,
    write_minimal_osm_fixture,
)
from geo_mcp.infrastructure.osm.pbf_store import PbfError, PbfStore


def test_parse_transit_from_fixture(tmp_path: Path) -> None:
    osm = write_minimal_osm_fixture(tmp_path / "tiny.osm")
    stops, lines = parse_transit_osm_xml(osm)
    assert {s.name for s in stops} >= {"Stop A", "Stop C"}
    assert any(line.ref == "12" for line in lines)


def test_offline_build_and_route(tmp_path: Path) -> None:
    osm = write_minimal_osm_fixture(tmp_path / "tiny.osm")
    settings = Settings(
        data_dir=tmp_path / "data",
        pbf_path=osm,
        offline=True,
        prefer_pbf=True,
        http_timeout_s=5,
    )
    # Force derived dirs
    settings.model_post_init(None)
    store = PbfStore(settings)
    builder = PbfNetworkBuilder(settings, store)
    assert builder.available()

    overpass = OverpassClient(settings)
    repo = OsmNetworkRepository(
        settings,
        overpass,
        cache=DiskGraphCache(settings),
        pbf=builder,
    )
    bbox = BoundingBox(south=-0.001, west=-0.001, north=0.003, east=0.003)
    graph = repo.load_area(bbox)
    assert graph.node_count > 0
    assert repo.using_pbf()

    # Should not need network — second load hits cache
    graph2 = repo.load_area(bbox)
    assert graph2.node_count == graph.node_count

    pf = PathfindingService()
    itin = pf.find_path(graph, GeoPoint(0.0, 0.0), GeoPoint(0.0, 0.002))
    assert itin.total_distance_m > 0
    overpass.close()


def test_offline_without_pbf_raises(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        pbf_path=tmp_path / "missing.osm.pbf",
        offline=True,
        prefer_pbf=True,
    )
    settings.model_post_init(None)
    store = PbfStore(settings)
    builder = PbfNetworkBuilder(settings, store)
    repo = OsmNetworkRepository(
        settings,
        OverpassClient(settings),
        cache=DiskGraphCache(settings),
        pbf=builder,
    )
    with pytest.raises(RuntimeError, match="offline"):
        repo.load_area(BoundingBox(0, 0, 0.01, 0.01))


def test_pbf_store_require_missing(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", pbf_region="ile-de-france")
    settings.model_post_init(None)
    store = PbfStore(settings)
    with pytest.raises(PbfError, match="ingest"):
        store.require_pbf()
