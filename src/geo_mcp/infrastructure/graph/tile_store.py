from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from typing import Literal

from geo_mcp.domain.deadline import Deadline
from geo_mcp.domain.model.geo import BoundingBox, GeoPoint, TileId
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, GraphNode, NetworkGraph
from geo_mcp.domain.model.transit import TransitLine, TransitStop
from geo_mcp.domain.services.transit_costs import speed_for_route_mode
from geo_mcp.infrastructure.config import Settings
from geo_mcp.infrastructure.osm.network_repository import OsmNetworkRepository

logger = logging.getLogger(__name__)

TileLayer = Literal["walk", "transit", "full"]


class TileGraphStore:
    """Parallel tiled graph loader with request-scoped merges."""

    def __init__(
        self,
        settings: Settings,
        networks: OsmNetworkRepository,
        *,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._networks = networks
        self._executor = executor or ThreadPoolExecutor(
            max_workers=settings.max_workers,
            thread_name_prefix="geo-tile",
        )
        self._owns_executor = executor is None
        self._memory: dict[str, NetworkGraph] = {}

    def close(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def tiles_for_point(self, point: GeoPoint, *, ring: int = 0) -> list[TileId]:
        center = TileId.from_point(point, self._settings.tile_size_deg)
        return center.neighbors(ring=ring)

    def tiles_for_bbox(
        self,
        bbox: BoundingBox,
        *,
        max_tiles: int | None = None,
    ) -> list[TileId]:
        return TileId.covering(
            bbox,
            self._settings.tile_size_deg,
            max_tiles=max_tiles or self._settings.max_walk_tiles,
        )

    def load_tiles(
        self,
        tiles: list[TileId],
        *,
        layer: TileLayer = "full",
        force_refresh: bool = False,
        deadline: Deadline | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> NetworkGraph:
        unique = list(dict.fromkeys(tiles))
        merged = NetworkGraph()
        if not unique:
            return merged

        futures = {
            self._executor.submit(self._load_one, tile, layer, force_refresh): tile
            for tile in unique
        }
        done = 0
        errors: list[str] = []
        for future in as_completed(futures):
            if deadline is not None:
                deadline.check("tile_load")
            tile = futures[future]
            done += 1
            try:
                graph = future.result()
                merged.merge_from(graph)
                if on_progress:
                    on_progress(done, len(unique), f"Loaded tile {tile.cache_key(layer)}")
            except Exception as exc:
                errors.append(f"{tile.cache_key(layer)}: {exc}")
                logger.warning("Tile load failed for %s: %s", tile.cache_key(layer), exc)
                if on_progress:
                    on_progress(done, len(unique), f"Failed tile {tile.cache_key(layer)}")

        if merged.node_count == 0 and errors:
            raise RuntimeError("All tile loads failed: " + "; ".join(errors[:3]))
        return merged

    def load_walk_around(
        self,
        point: GeoPoint,
        *,
        ring: int = 1,
        deadline: Deadline | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> NetworkGraph:
        return self.load_tiles(
            self.tiles_for_point(point, ring=ring),
            layer="full",
            deadline=deadline,
            on_progress=on_progress,
        )

    def load_transit_skeleton(
        self,
        bbox: BoundingBox,
        *,
        deadline: Deadline | None = None,
    ) -> tuple[NetworkGraph, list[TransitStop], list[TransitLine]]:
        if deadline is not None:
            deadline.check("transit_skeleton")
        stops, lines = self._networks.fetch_transit(bbox)
        if self._settings.offline and not stops:
            logger.warning("Transit skeleton empty in offline/PBF mode for %s", bbox)
        graph = NetworkGraph(bbox=bbox.rounded())
        for stop in stops:
            graph.add_node(
                GraphNode(
                    id=stop.id,
                    point=stop.point,
                    kind="stop",
                    name=stop.name,
                    tags={
                        "modes": ",".join(stop.modes),
                        "lines": ",".join(stop.lines),
                    },
                )
            )
        self._attach_transit_edges(graph, lines)
        self._networks._link_stop_transfers(graph)
        return graph, stops, lines

    def _load_one(
        self,
        tile: TileId,
        layer: TileLayer,
        force_refresh: bool,
    ) -> NetworkGraph:
        key = tile.cache_key(layer)
        if not force_refresh and key in self._memory:
            return self._memory[key]
        bbox = tile.bbox()
        if layer == "transit":
            graph, _, _ = self.load_transit_skeleton(bbox)
        else:
            graph = self._networks.load_area(bbox, force_refresh=force_refresh)
        self._memory[key] = graph
        return graph

    def _attach_transit_edges(
        self,
        graph: NetworkGraph,
        lines: list[TransitLine],
    ) -> None:
        for line in lines:
            stop_ids = list(dict.fromkeys(sid for sid in line.stop_ids if sid in graph.nodes))
            if line.mode == "ferry" and len(stop_ids) >= 2:
                pairs = [
                    (a, b)
                    for i, a in enumerate(stop_ids)
                    for b in stop_ids[i + 1 :]
                ]
            else:
                pairs = list(zip(stop_ids, stop_ids[1:], strict=False))
            for a, b in pairs:
                pa = graph.nodes[a].point
                pb = graph.nodes[b].point
                length = pa.distance_meters(pb)
                if length <= 0:
                    continue
                speed = speed_for_route_mode(
                    line.mode,
                    default_mps=self._settings.transit_speed_mps,
                    ferry_mps=self._settings.ferry_speed_mps,
                )
                travel = length / speed
                if line.mode in {"bus", "trolleybus"}:
                    travel *= getattr(self._settings, "bus_time_factor", 2.0)
                for src, tgt in ((a, b), (b, a)):
                    graph.add_edge(
                        GraphEdge(
                            source_id=src,
                            target_id=tgt,
                            mode=EdgeMode.TRANSIT,
                            length_m=length,
                            travel_time_s=travel,
                            name=line.name,
                            line_ref=line.ref,
                            tags={"route": line.mode},
                        )
                    )
        # Also unify fragmented rail branches (same as network repository).
        self._networks._link_rail_by_ref(graph, lines)
