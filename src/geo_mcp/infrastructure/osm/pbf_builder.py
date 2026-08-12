from __future__ import annotations

import logging
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import osmnx as ox

from geo_mcp.domain.model.geo import BoundingBox, GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.domain.model.transit import TransitLine, TransitStop
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.osm.pbf_store import PbfError, PbfStore

logger = logging.getLogger(__name__)


class PbfNetworkBuilder:
    """Build walk+transit graphs from a local .osm.pbf / .osm extract."""

    def __init__(self, settings: Settings, store: PbfStore) -> None:
        self._settings = settings
        self._store = store

    def available(self) -> bool:
        return self._store.resolve_pbf() is not None

    def extract_xml(self, bbox: BoundingBox, *, source: Path | None = None) -> Path:
        """Clip region PBF to bbox and materialize OSM XML for OSMnx/parsers."""
        source = source or self._store.require_pbf()
        # Plain OSM XML can be used directly (tests / small fixtures).
        if source.suffix == ".osm" or source.name.endswith(".osm.xml"):
            return source

        osmium = self._store.require_osmium()
        key = bbox.rounded().cache_key()
        out_pbf = self._store.extract_dir / f"clip_smart_{key}.osm.pbf"
        out_xml = self._store.extract_dir / f"clip_smart_{key}.osm"
        if out_xml.exists() and out_xml.stat().st_mtime >= source.stat().st_mtime:
            return out_xml

        # osmium extract bbox: west,south,east,north
        bbox_arg = f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}"
        cmd_extract = [
            osmium,
            "extract",
            "--strategy",
            "smart",
            "-b",
            bbox_arg,
            "-o",
            str(out_pbf),
            "--overwrite",
            str(source),
        ]
        logger.info("Extracting bbox %s from %s", key, source.name)
        result = subprocess.run(cmd_extract, capture_output=True, text=True)
        if result.returncode != 0:
            raise PbfError(f"osmium extract failed: {result.stderr.strip()}")

        cmd_cat = [
            osmium,
            "cat",
            "-o",
            str(out_xml),
            "--overwrite",
            str(out_pbf),
        ]
        result = subprocess.run(cmd_cat, capture_output=True, text=True)
        if result.returncode != 0:
            raise PbfError(f"osmium cat failed: {result.stderr.strip()}")
        return out_xml

    def build_walk_graph(self, bbox: BoundingBox, *, source: Path | None = None) -> NetworkGraph:
        xml_path = self.extract_xml(bbox, source=source)
        # OSMnx reads local XML — no Overpass.
        g = ox.graph_from_xml(xml_path, simplify=True, retain_all=False)
        # Keep only the largest weakly connected component when possible.
        try:
            import networkx as nx

            if not nx.is_weakly_connected(g):
                largest = max(nx.weakly_connected_components(g), key=len)
                g = g.subgraph(largest).copy()
        except Exception:
            logger.debug("Could not prune walk graph components", exc_info=True)

        graph = NetworkGraph(bbox=bbox.rounded())
        for node_id, data in g.nodes(data=True):
            if "y" not in data or "x" not in data:
                continue
            graph.add_node(
                GraphNode(
                    id=f"n:{node_id}",
                    point=GeoPoint(lat=float(data["y"]), lon=float(data["x"])),
                    kind="intersection",
                    name=data.get("name"),
                    tags={
                        k: str(v)
                        for k, v in data.items()
                        if k not in {"y", "x"} and isinstance(v, (str, int, float))
                    },
                )
            )
        walk_speed = self._settings.walk_speed_mps
        for u, v, data in g.edges(data=True):
            if f"n:{u}" not in graph.nodes or f"n:{v}" not in graph.nodes:
                continue
            length = float(data.get("length") or 0.0)
            if length <= 0:
                # Fall back to haversine if length missing.
                length = graph.nodes[f"n:{u}"].point.distance_meters(
                    graph.nodes[f"n:{v}"].point
                )
            if length <= 0:
                continue
            name = data.get("name")
            if isinstance(name, list):
                name = name[0] if name else None
            travel = length / walk_speed
            graph.add_edge(
                GraphEdge(
                    source_id=f"n:{u}",
                    target_id=f"n:{v}",
                    mode=EdgeMode.WALK,
                    length_m=length,
                    travel_time_s=travel,
                    name=str(name) if name else None,
                    tags={
                        "highway": str(data["highway"])
                        if "highway" in data
                        else "footway"
                    },
                )
            )
        return graph

    def fetch_transit(
        self, bbox: BoundingBox, *, source: Path | None = None
    ) -> tuple[list[TransitStop], list[TransitLine]]:
        xml_path = self.extract_xml(bbox, source=source)
        return parse_transit_osm_xml(xml_path)


def parse_transit_osm_xml(xml_path: Path) -> tuple[list[TransitStop], list[TransitLine]]:
    """Parse stops + route relations from OSM XML (same semantics as Overpass client)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    nodes_by_id: dict[str, ET.Element] = {}
    for node in root.findall("node"):
        nodes_by_id[node.attrib["id"]] = node

    stops: dict[str, TransitStop] = {}
    for nid, el in nodes_by_id.items():
        tags = {t.attrib["k"]: t.attrib["v"] for t in el.findall("tag")}
        is_stop = any(
            [
                tags.get("highway") == "bus_stop",
                tags.get("public_transport") in {"platform", "stop_position"},
                tags.get("railway") in {"station", "halt", "tram_stop"},
                tags.get("amenity") in {"bus_station", "ferry_terminal"},
            ]
        )
        if not is_stop:
            continue
        stop_id = f"stop:{nid}"
        modes: list[str] = []
        if tags.get("highway") == "bus_stop" or tags.get("amenity") == "bus_station":
            modes.append("bus")
        if tags.get("amenity") == "ferry_terminal":
            modes.append("ferry")
        if tags.get("railway") in {"station", "halt"}:
            modes.append("rail")
        if tags.get("railway") == "tram_stop":
            modes.append("tram")
        if tags.get("public_transport"):
            modes.append(tags["public_transport"])
        stops[stop_id] = TransitStop(
            id=stop_id,
            name=tags.get("name") or tags.get("ref") or stop_id,
            point=GeoPoint(lat=float(el.attrib["lat"]), lon=float(el.attrib["lon"])),
            modes=tuple(dict.fromkeys(modes)) or ("transit",),
            osm_id=nid,
        )

    lines: list[TransitLine] = []
    for rel in root.findall("relation"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in rel.findall("tag")}
        if tags.get("type") != "route":
            continue
        route_mode = tags.get("route")
        if route_mode not in {
            "bus",
            "tram",
            "subway",
            "train",
            "light_rail",
            "ferry",
        }:
            continue
        ref = tags.get("ref") or tags.get("name") or f"route:{rel.attrib['id']}"
        stop_ids: list[str] = []
        for member in rel.findall("member"):
            if member.attrib.get("type") != "node":
                continue
            role = member.attrib.get("role") or ""
            if role and role not in {
                "stop",
                "platform",
                "stop_entry_only",
                "stop_exit_only",
            }:
                continue
            nid = member.attrib.get("ref")
            if not nid:
                continue
            sid = f"stop:{nid}"
            if sid not in stops and nid in nodes_by_id:
                node = nodes_by_id[nid]
                ntags = {t.attrib["k"]: t.attrib["v"] for t in node.findall("tag")}
                stops[sid] = TransitStop(
                    id=sid,
                    name=ntags.get("name") or ntags.get("ref") or sid,
                    point=GeoPoint(
                        lat=float(node.attrib["lat"]), lon=float(node.attrib["lon"])
                    ),
                    modes=(route_mode,),
                    osm_id=nid,
                )
            if sid in stops:
                stop_ids.append(sid)
                existing = stops[sid]
                lines_set = set(existing.lines) | {ref}
                modes_set = set(existing.modes) | {route_mode}
                stops[sid] = TransitStop(
                    id=existing.id,
                    name=existing.name,
                    point=existing.point,
                    modes=tuple(modes_set),
                    lines=tuple(sorted(lines_set)),
                    osm_id=existing.osm_id,
                )
        if stop_ids:
            lines.append(
                TransitLine(
                    ref=ref,
                    name=tags.get("name"),
                    mode=route_mode,
                    stop_ids=tuple(dict.fromkeys(stop_ids)),
                    tags={k: str(v) for k, v in tags.items()},
                )
            )
    return list(stops.values()), lines


def write_minimal_osm_fixture(path: Path) -> Path:
    """Tiny walk corridor + one transit line for offline unit tests."""
    # Roughly 0,0 → 0,0.002 with a bus shortcut.
    content = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='geo-mcp-test'>
  <node id='1' lat='0.0' lon='0.0'/>
  <node id='2' lat='0.0' lon='0.001'/>
  <node id='3' lat='0.0' lon='0.002'/>
  <node id='10' lat='0.0001' lon='0.0'>
    <tag k='highway' v='bus_stop'/>
    <tag k='name' v='Stop A'/>
  </node>
  <node id='11' lat='0.0001' lon='0.002'>
    <tag k='highway' v='bus_stop'/>
    <tag k='name' v='Stop C'/>
  </node>
  <way id='100'>
    <nd ref='1'/><nd ref='2'/>
    <tag k='highway' v='residential'/>
    <tag k='name' v='Main St'/>
  </way>
  <way id='101'>
    <nd ref='2'/><nd ref='3'/>
    <tag k='highway' v='residential'/>
    <tag k='name' v='Main St'/>
  </way>
  <relation id='200'>
    <member type='node' ref='10' role='stop'/>
    <member type='node' ref='11' role='stop'/>
    <tag k='type' v='route'/>
    <tag k='route' v='bus'/>
    <tag k='ref' v='12'/>
    <tag k='name' v='Bus 12'/>
  </relation>
</osm>
"""
    path.write_text(content, encoding="utf-8")
    return path
