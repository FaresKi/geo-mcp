from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Well-known Geofabrik extracts (override with GEO_MCP_PBF_URL).
GEOFABRIK_REGIONS: dict[str, str] = {
    "ile-de-france": (
        "https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf"
    ),
    "france": "https://download.geofabrik.de/europe/france-latest.osm.pbf",
    "berlin": "https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf",
    "greater-london": (
        "https://download.geofabrik.de/europe/great-britain/england/"
        "greater-london-latest.osm.pbf"
    ),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_MCP_")

    user_agent: str = "geo-mcp/0.3.0 (platform-agnostic OSM navigation MCP)"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass.kumi.systems/api/interpreter"
    overpass_mirrors: str = (
        "https://overpass.kumi.systems/api/interpreter,"
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.nchc.org.tw/api/interpreter"
    )
    data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "geo-mcp"
    )
    cache_dir: Path | None = None  # defaults to data_dir/graphs
    pbf_dir: Path | None = None  # defaults to data_dir/pbf
    pbf_path: Path | None = None  # explicit local .osm.pbf / .osm
    pbf_url: str | None = None
    pbf_region: str = "ile-de-france"
    offline: bool = False  # never call Overpass; require PBF or graph cache
    prefer_pbf: bool = True  # use local PBF when present
    http_timeout_s: float = 90.0
    overpass_retries: int = 2
    nominatim_retries: int = 2
    retry_base_delay_s: float = 0.25
    retry_max_delay_s: float = 8.0
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout_s: float = 30.0
    walk_speed_mps: float = 1.4
    transit_speed_mps: float = 8.0
    ferry_speed_mps: float = 6.5
    transfer_penalty_s: float = 60.0
    line_change_penalty_s: float = 180.0  # discourage bus hop-scotch
    prefer_rail_access: bool = True
    bus_time_factor: float = 2.5  # inflate bus travel times vs rail
    default_area_half_size_deg: float = 0.01
    max_transfer_snap_m: float = 250.0
    stop_transfer_snap_m: float = 400.0  # platform↔platform at interchanges
    max_snap_m: float = 400.0
    local_max_m: float = 2_000.0
    access_radius_m: float = 800.0
    tile_size_deg: float = 0.01
    max_walk_tiles: int = 9
    transit_bbox_pad_deg: float = 0.06
    transit_min_span_deg: float = 0.08  # ensure corridor wide enough for hub stations
    max_workers: int = 8
    sync_budget_s: float = 25.0
    operation_deadline_s: float = 120.0
    job_ttl_s: float = 3_600.0
    place_bbox_max_span_deg: float = 0.05

    def model_post_init(self, __context: object) -> None:
        if self.cache_dir is None:
            object.__setattr__(self, "cache_dir", self.data_dir / "graphs")
        if self.pbf_dir is None:
            object.__setattr__(self, "pbf_dir", self.data_dir / "pbf")

    def resolved_pbf_url(self) -> str:
        if self.pbf_url:
            return self.pbf_url
        if self.pbf_region in GEOFABRIK_REGIONS:
            return GEOFABRIK_REGIONS[self.pbf_region]
        raise ValueError(
            f"Unknown pbf_region={self.pbf_region!r}; "
            f"known={sorted(GEOFABRIK_REGIONS)} or set GEO_MCP_PBF_URL"
        )


def get_settings() -> Settings:
    return Settings()
