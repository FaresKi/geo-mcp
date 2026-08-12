from __future__ import annotations

from geo_mcp.container import build_container
from geo_mcp.infrastructure.config import Settings
from geo_mcp.interfaces.mcp.server import create_mcp_server


def test_mcp_registers_job_tools() -> None:
    settings = Settings()
    container = build_container(settings)
    mcp = create_mcp_server(container)
    tool_manager = mcp._tool_manager
    names = {tool.name for tool in tool_manager.list_tools()}
    expected = {
        "plan_route",
        "submit_route",
        "get_job",
        "cancel_job",
        "list_jobs",
        "load_area",
        "geocode",
    }
    assert expected <= names
    container.close()
