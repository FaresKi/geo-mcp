from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_MCP_")

    user_agent: str = "geo-mcp/0.1.0 (platform-agnostic OSM navigation MCP)"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass.kumi.systems/api/interpreter"
    overpass_mirrors: str = (
        "https://overpass.kumi.systems/api/interpreter,"
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.nchc.org.tw/api/interpreter"
    )
    cache_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "geo-mcp" / "graphs"
    )
    http_timeout_s: float = 90.0
    overpass_retries: int = 2
    walk_speed_mps: float = 1.4
    transit_speed_mps: float = 8.0
    ferry_speed_mps: float = 6.5  # ~23 km/h harbor ferry heuristic
    transfer_penalty_s: float = 60.0
    default_area_half_size_deg: float = 0.01  # ~1km
    max_transfer_snap_m: float = 250.0


def get_settings() -> Settings:
    return Settings()
