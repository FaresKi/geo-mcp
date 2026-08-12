from __future__ import annotations

import heapq
from collections.abc import Iterable

from geo_mcp.domain.model.geo import GeoPoint
from geo_mcp.domain.model.network import EdgeMode, GraphEdge, NetworkGraph
from geo_mcp.domain.model.route import Itinerary, RouteLeg
from geo_mcp.domain.services.transit_costs import ROUTE_SPEED_MPS


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
        line_change_penalty_s: float = 180.0,
    ) -> None:
        self.walk_speed_mps = walk_speed_mps
        self.transit_speed_mps = transit_speed_mps
        self.transfer_penalty_s = transfer_penalty_s
        # Penalize hopping between different line_ref values mid-journey.
        self.line_change_penalty_s = line_change_penalty_s

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

        origin_match = graph.nearest_node(origin, max_radius_m=max_snap_m)
        dest_match = graph.nearest_node(destination, max_radius_m=max_snap_m)
        if origin_match is None or dest_match is None:
            raise PathfindingError(
                f"Could not snap endpoints to network within {max_snap_m}m"
            )

        start, _ = origin_match
        end, _ = dest_match
        path_edges = self._astar(graph, {start.id: 0.0}, {end.id}, modes)
        if path_edges is None:
            raise PathfindingError("No path found between snapped nodes")

        return self._build_itinerary(graph, path_edges, start.id, end.id)

    def find_path_between_sets(
        self,
        graph: NetworkGraph,
        sources: dict[str, float],
        goals: set[str],
        *,
        allowed_modes: Iterable[EdgeMode] | None = None,
        goal_point: GeoPoint | None = None,
    ) -> Itinerary:
        """Multi-source / multi-sink search. ``sources`` maps node_id → seed cost."""
        modes = set(allowed_modes or (EdgeMode.WALK, EdgeMode.TRANSIT, EdgeMode.TRANSFER))
        if EdgeMode.TRANSIT in modes:
            modes.add(EdgeMode.TRANSFER)
        if not sources or not goals:
            raise PathfindingError("Empty source or goal set")
        path_edges = self._astar(graph, sources, goals, modes, goal_point=goal_point)
        if path_edges is None:
            raise PathfindingError("No path found between stop sets")
        start_id = path_edges[0].source_id if path_edges else next(iter(sources))
        end_id = path_edges[-1].target_id if path_edges else next(iter(goals))
        if not path_edges:
            start_id = next(iter(sources.keys() & goals))
            end_id = start_id
        return self._build_itinerary(graph, path_edges, start_id, end_id)

    def _edge_cost(
        self,
        edge: GraphEdge,
        *,
        prev_line: str | None,
    ) -> float:
        cost = edge.travel_time_s
        if edge.mode == EdgeMode.TRANSFER:
            return cost + self.transfer_penalty_s
        if (
            edge.mode == EdgeMode.TRANSIT
            and prev_line is not None
            and edge.line_ref
            and edge.line_ref != prev_line
        ):
            return cost + self.line_change_penalty_s
        return cost

    def _heuristic_speed(self, modes: set[EdgeMode]) -> float:
        if EdgeMode.TRANSIT not in modes:
            return self.walk_speed_mps
        # Admissible: use the fastest configured rail speed.
        return max(self.transit_speed_mps, max(ROUTE_SPEED_MPS.values()))

    def _astar(
        self,
        graph: NetworkGraph,
        sources: dict[str, float],
        goals: set[str],
        modes: set[EdgeMode],
        *,
        goal_point: GeoPoint | None = None,
    ) -> list | None:
        if goal_point is None:
            pts = [graph.nodes[g].point for g in goals if g in graph.nodes]
            if pts:
                goal_point = GeoPoint(
                    lat=sum(p.lat for p in pts) / len(pts),
                    lon=sum(p.lon for p in pts) / len(pts),
                )

        h_speed = self._heuristic_speed(modes)

        def heuristic(node_id: str) -> float:
            if goal_point is None:
                return 0.0
            node = graph.nodes[node_id]
            return node.point.distance_meters(goal_point) / h_speed

        # State = (node_id, active_line) where active_line is "" when not on a line.
        open_heap: list[tuple[float, float, str, str]] = []
        came_from: dict[tuple[str, str], tuple[tuple[str, str], GraphEdge]] = {}
        g_score: dict[tuple[str, str], float] = {}
        for sid, seed in sources.items():
            if sid not in graph.nodes:
                continue
            state = (sid, "")
            g_score[state] = seed
            heapq.heappush(
                open_heap, (seed + heuristic(sid), seed, sid, "")
            )

        if not open_heap:
            return None

        closed: set[tuple[str, str]] = set()
        best_goal_state: tuple[str, str] | None = None
        best_goal_cost = float("inf")

        while open_heap:
            _, cost, current, line = heapq.heappop(open_heap)
            state = (current, line)
            if state in closed:
                continue
            if current in goals and cost < best_goal_cost:
                best_goal_state = state
                best_goal_cost = cost
                if open_heap and open_heap[0][0] >= best_goal_cost:
                    break
                if len(goals) == 1:
                    break
            closed.add(state)

            prev_line = line or None
            for edge in graph.neighbors(current):
                if edge.mode not in modes:
                    continue
                tentative = cost + self._edge_cost(edge, prev_line=prev_line)
                neighbor = edge.target_id
                if edge.mode == EdgeMode.TRANSIT and edge.line_ref:
                    next_line = edge.line_ref
                else:
                    next_line = ""
                nstate = (neighbor, next_line)
                if tentative >= g_score.get(nstate, float("inf")):
                    continue
                g_score[nstate] = tentative
                came_from[nstate] = (state, edge)
                f = tentative + heuristic(neighbor)
                heapq.heappush(
                    open_heap, (f, tentative, neighbor, next_line)
                )

        if best_goal_state is None:
            return None
        if best_goal_state[0] in sources and best_goal_state not in came_from:
            return []
        return self._reconstruct(came_from, best_goal_state)

    def _reconstruct(
        self,
        came_from: dict[tuple[str, str], tuple[tuple[str, str], GraphEdge]],
        current: tuple[str, str],
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
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

        transfer_count = self._count_transfers(legs)
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

    def _count_transfers(self, legs: list[RouteLeg]) -> int:
        count = sum(1 for leg in legs if leg.mode == EdgeMode.TRANSFER)
        prev_line: str | None = None
        for leg in legs:
            if leg.mode == EdgeMode.TRANSIT and leg.line_ref:
                if prev_line is not None and leg.line_ref != prev_line:
                    count += 1
                prev_line = leg.line_ref
            elif leg.mode in {EdgeMode.WALK, EdgeMode.TRANSFER}:
                # Walking / platform transfer breaks the continuous line ride.
                if leg.mode == EdgeMode.TRANSFER:
                    prev_line = None
        return count

    def stitch(self, *parts: Itinerary) -> Itinerary:
        """Concatenate itineraries into one narrative journey."""
        legs: list[RouteLeg] = []
        node_ids: list[str] = []
        for part in parts:
            if not part.legs:
                continue
            legs.extend(part.legs)
            if not node_ids:
                node_ids.extend(part.node_ids)
            else:
                node_ids.extend(part.node_ids[1:] if part.node_ids else [])
        if not legs:
            raise PathfindingError("Nothing to stitch")
        transfer_count = self._count_transfers(legs)
        total_distance = sum(leg.distance_m for leg in legs)
        total_duration = sum(leg.duration_s for leg in legs) + (
            transfer_count * self.transfer_penalty_s
        )
        modes_used = tuple(dict.fromkeys(leg.mode.value for leg in legs))
        return Itinerary(
            legs=tuple(legs),
            total_distance_m=total_distance,
            total_duration_s=total_duration,
            transfer_count=transfer_count,
            narrative=self._narrative(legs, total_duration),
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
