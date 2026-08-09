from __future__ import annotations

import heapq
from collections.abc import Iterable

from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.network import EdgeMode, NetworkGraph
from geo_mcp.domain.model.route import Itinerary, RouteLeg


class PathfindingError(Exception):
    """Raised when no path exists between endpoints."""


class PathfindingService:
    """Pure multimodal shortest-path search on a NetworkGraph."""

    def __init__(
        self,
        *,
        walk_speed_mps: float = 1.4,
        transit_speed_mps: float = 8.0,
        transfer_penalty_s: float = 60.0,
    ) -> None:
        self.walk_speed_mps = walk_speed_mps
        self.transit_speed_mps = transit_speed_mps
        self.transfer_penalty_s = transfer_penalty_s

    def find_path(
        self,
        graph: NetworkGraph,
        origin: GeoPoint,
        destination: GeoPoint,
        *,
        allowed_modes: Iterable[EdgeMode] | None = None,
        max_snap_m: float = 400.0,
    ) -> Itinerary:
        modes = set(allowed_modes or (EdgeMode.WALK, EdgeMode.TRANSIT, EdgeMode.TRANSFER))
        if EdgeMode.TRANSIT in modes:
            modes.add(EdgeMode.TRANSFER)

        origin_match = graph.nearest_node(origin)
        dest_match = graph.nearest_node(destination)
        if origin_match is None or dest_match is None:
            raise PathfindingError("Graph has no nodes to snap to")

        start, start_dist = origin_match
        end, end_dist = dest_match
        if start_dist > max_snap_m or end_dist > max_snap_m:
            raise PathfindingError(
                f"Could not snap endpoints to network within {max_snap_m}m "
                f"(origin={start_dist:.0f}m, dest={end_dist:.0f}m)"
            )

        path_edges = self._astar(graph, start.id, end.id, modes)
        if path_edges is None:
            raise PathfindingError("No path found between snapped nodes")

        return self._build_itinerary(graph, path_edges, start.id, end.id)

    def _edge_cost(self, mode: EdgeMode, travel_time_s: float) -> float:
        if mode == EdgeMode.TRANSFER:
            return travel_time_s + self.transfer_penalty_s
        return travel_time_s

    def _astar(
        self,
        graph: NetworkGraph,
        start_id: str,
        goal_id: str,
        modes: set[EdgeMode],
    ) -> list | None:
        goal = graph.nodes[goal_id]

        def heuristic(node_id: str) -> float:
            node = graph.nodes[node_id]
            speed = (
                self.transit_speed_mps
                if EdgeMode.TRANSIT in modes
                else self.walk_speed_mps
            )
            return node.point.distance_meters(goal.point) / speed

        open_heap: list[tuple[float, float, str]] = [(heuristic(start_id), 0.0, start_id)]
        came_from: dict[str, tuple[str, object]] = {}
        g_score: dict[str, float] = {start_id: 0.0}
        closed: set[str] = set()

        while open_heap:
            _, cost, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            if current == goal_id:
                return self._reconstruct(came_from, current)
            closed.add(current)

            for edge in graph.neighbors(current):
                if edge.mode not in modes:
                    continue
                tentative = cost + self._edge_cost(edge.mode, edge.travel_time_s)
                neighbor = edge.target_id
                if tentative >= g_score.get(neighbor, float("inf")):
                    continue
                g_score[neighbor] = tentative
                came_from[neighbor] = (current, edge)
                f = tentative + heuristic(neighbor)
                heapq.heappush(open_heap, (f, tentative, neighbor))

        return None

    def _reconstruct(self, came_from: dict, current: str) -> list:
        edges = []
        while current in came_from:
            prev, edge = came_from[current]
            edges.append(edge)
            current = prev
        edges.reverse()
        return edges

    def _build_itinerary(
        self,
        graph: NetworkGraph,
        edges: list,
        start_id: str,
        end_id: str,
    ) -> Itinerary:
        if not edges:
            start = graph.nodes[start_id]
            end = graph.nodes[end_id]
            dist = start.point.distance_meters(end.point)
            leg = RouteLeg(
                mode=EdgeMode.WALK,
                distance_m=dist,
                duration_s=dist / self.walk_speed_mps,
                from_name=start.name,
                to_name=end.name,
                geometry=(start.point, end.point),
                instruction="Stay in place" if dist < 1 else "Walk to destination",
            )
            return Itinerary(
                legs=(leg,),
                total_distance_m=dist,
                total_duration_s=leg.duration_s,
                transfer_count=0,
                narrative=leg.instruction,
                node_ids=(start_id, end_id),
                modes_used=("walk",),
            )

        raw_groups: list[list] = [[edges[0]]]
        for edge in edges[1:]:
            prev = raw_groups[-1][-1]
            same = (
                edge.mode == prev.mode
                and edge.line_ref == prev.line_ref
                and edge.mode != EdgeMode.TRANSFER
            )
            if same:
                raw_groups[-1].append(edge)
            else:
                raw_groups.append([edge])

        legs: list[RouteLeg] = []
        node_ids = [start_id]
        for group in raw_groups:
            first, last = group[0], group[-1]
            from_node = graph.nodes[first.source_id]
            to_node = graph.nodes[last.target_id]
            distance = sum(e.length_m for e in group)
            duration = sum(e.travel_time_s for e in group)
            street = next((e.name for e in group if e.name), None)
            geometry = tuple(
                [from_node.point] + [graph.nodes[e.target_id].point for e in group]
            )
            instruction = self._instruction(
                mode=first.mode,
                from_name=from_node.name or street or from_node.id,
                to_name=to_node.name or to_node.id,
                street=street,
                line_ref=first.line_ref,
                distance_m=distance,
            )
            legs.append(
                RouteLeg(
                    mode=first.mode,
                    distance_m=distance,
                    duration_s=duration,
                    from_name=from_node.name,
                    to_name=to_node.name,
                    street_name=street,
                    line_ref=first.line_ref,
                    geometry=geometry,
                    instruction=instruction,
                )
            )
            node_ids.append(last.target_id)

        transfer_count = sum(1 for leg in legs if leg.mode == EdgeMode.TRANSFER)
        total_distance = sum(leg.distance_m for leg in legs)
        total_duration = sum(leg.duration_s for leg in legs) + (
            transfer_count * self.transfer_penalty_s
        )
        modes_used = tuple(dict.fromkeys(leg.mode.value for leg in legs))
        narrative = self._narrative(legs, total_duration)

        return Itinerary(
            legs=tuple(legs),
            total_distance_m=total_distance,
            total_duration_s=total_duration,
            transfer_count=transfer_count,
            narrative=narrative,
            node_ids=tuple(node_ids),
            modes_used=modes_used,
        )

    def _instruction(
        self,
        *,
        mode: EdgeMode,
        from_name: str,
        to_name: str,
        street: str | None,
        line_ref: str | None,
        distance_m: float,
    ) -> str:
        dist = f"{distance_m:.0f} m"
        if mode == EdgeMode.WALK:
            via = f" via {street}" if street else ""
            return f"Walk {dist}{via} from {from_name} to {to_name}"
        if mode == EdgeMode.TRANSFER:
            return f"Transfer ({dist}) from {from_name} to {to_name}"
        line = f" line {line_ref}" if line_ref else ""
        return f"Take transit{line} {dist} from {from_name} to {to_name}"

    def _narrative(self, legs: list[RouteLeg], total_duration_s: float) -> str:
        minutes = max(1, int(round(total_duration_s / 60)))
        steps = "; ".join(leg.instruction for leg in legs)
        return f"About {minutes} min total. {steps}."
