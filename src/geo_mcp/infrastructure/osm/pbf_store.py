from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path

import httpx

from geo_mcp.infrastructure.config import GEOFABRIK_REGIONS, Settings

logger = logging.getLogger(__name__)


class PbfError(RuntimeError):
    """Raised when local PBF ingest/extract fails."""


@dataclass(frozen=True, slots=True)
class PbfArtifact:
    path: Path
    source_url: str | None
    downloaded_at: str | None
    size_bytes: int

    def to_meta(self) -> dict:
        return {
            "path": str(self.path),
            "source_url": self.source_url,
            "downloaded_at": self.downloaded_at,
            "size_bytes": self.size_bytes,
        }


class PbfStore:
    """Download and locate Geofabrik (or custom) OSM PBF extracts."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dir = settings.pbf_dir or (settings.data_dir / "pbf")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._extract_dir = settings.data_dir / "extracts"
        self._extract_dir.mkdir(parents=True, exist_ok=True)

    @property
    def extract_dir(self) -> Path:
        return self._extract_dir

    def meta_path_for(self, pbf: Path) -> Path:
        return pbf.with_suffix(pbf.suffix + ".meta.json")

    def default_pbf_path(self, region: str | None = None) -> Path:
        region = region or self._settings.pbf_region
        return self._dir / f"{region}-latest.osm.pbf"

    def resolve_pbf(self) -> Path | None:
        """Return configured/local PBF (or .osm) path if it exists."""
        if self._settings.pbf_path and self._settings.pbf_path.exists():
            return self._settings.pbf_path
        candidate = self.default_pbf_path()
        if candidate.exists():
            return candidate
        matches = sorted(self._dir.glob("*.osm.pbf")) + sorted(self._dir.glob("*.osm"))
        return matches[0] if matches else None

    def require_pbf(self) -> Path:
        path = self.resolve_pbf()
        if path is None:
            raise PbfError(
                "No local OSM extract found. Run: geo-mcp ingest "
                f"--region {self._settings.pbf_region}"
            )
        return path

    def has_osmium(self) -> bool:
        return shutil.which("osmium") is not None

    def require_osmium(self) -> str:
        exe = shutil.which("osmium")
        if not exe:
            raise PbfError(
                "osmium CLI not found. Install with: brew install osmium-tool"
            )
        return exe

    def download(
        self,
        *,
        region: str | None = None,
        url: str | None = None,
        force: bool = False,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> PbfArtifact:
        region = region or self._settings.pbf_region
        url = url or self._settings.pbf_url or GEOFABRIK_REGIONS.get(region)
        if not url:
            raise PbfError(f"No download URL for region={region!r}")
        dest = self.default_pbf_path(region)
        if dest.exists() and not force:
            meta = self._read_meta(dest)
            logger.info("PBF already present at %s (use --force to re-download)", dest)
            return PbfArtifact(
                path=dest,
                source_url=meta.get("source_url", url),
                downloaded_at=meta.get("downloaded_at"),
                size_bytes=dest.stat().st_size,
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".partial")
        logger.info("Downloading %s → %s", url, dest)
        downloaded = 0
        with httpx.stream(
            "GET",
            url,
            headers={"User-Agent": self._settings.user_agent},
            timeout=None,
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes(1024 * 1024):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total or None)
        tmp.replace(dest)
        artifact = PbfArtifact(
            path=dest,
            source_url=url,
            downloaded_at=datetime.now(tz=timezone.utc).isoformat(),
            size_bytes=dest.stat().st_size,
        )
        self.meta_path_for(dest).write_text(
            json.dumps(artifact.to_meta(), indent=2), encoding="utf-8"
        )
        logger.info("Downloaded %.1f MB", artifact.size_bytes / 1e6)
        return artifact

    def _read_meta(self, pbf: Path) -> dict:
        meta = self.meta_path_for(pbf)
        if not meta.exists():
            return {}
        try:
            return json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            return {}
