from __future__ import annotations

from collections import defaultdict

from geo_mcp.domain.model.geo import BoundingBox
from geo_mcp.domain.model.network import NetworkGraph


class AreaSession:
    """Tracks the active loaded area for MCP tool calls within a process."""

    def __init__(self) -> None:
        self.graph: NetworkGraph | None = None
        self.bbox: BoundingBox | None = None
        self.label: str | None = None
        self.line_index: dict[str, list[str]] = defaultdict(list)
