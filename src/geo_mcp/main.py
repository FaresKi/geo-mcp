from __future__ import annotations

import argparse
import logging
import sys

from geo_mcp.infrastructure.config import GEOFABRIK_REGIONS, get_settings
from geo_mcp.infrastructure.osm.pbf_store import PbfStore
from geo_mcp.interfaces.mcp.server import create_mcp_server


def _cmd_serve(args: argparse.Namespace) -> None:
    mcp = create_mcp_server(host=args.host, port=args.port)
    mcp.run(transport=args.transport)


def _cmd_ingest(args: argparse.Namespace) -> None:
    base = get_settings()
    overrides: dict = {}
    if args.region:
        overrides["pbf_region"] = args.region
    if args.url:
        overrides["pbf_url"] = args.url
    settings = base.model_copy(update=overrides) if overrides else base

    store = PbfStore(settings)

    def progress(downloaded: int, total: int | None) -> None:
        if total:
            pct = 100.0 * downloaded / total
            print(
                f"\rDownloading… {downloaded/1e6:.1f}/{total/1e6:.1f} MB ({pct:.0f}%)",
                end="",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                f"\rDownloading… {downloaded/1e6:.1f} MB",
                end="",
                file=sys.stderr,
                flush=True,
            )

    artifact = store.download(
        region=args.region,
        url=args.url,
        force=args.force,
        on_progress=progress,
    )
    print(file=sys.stderr)
    print(f"PBF ready: {artifact.path}")
    print(f"Size: {artifact.size_bytes/1e6:.1f} MB")
    if artifact.source_url:
        print(f"Source: {artifact.source_url}")
    if artifact.downloaded_at:
        print(f"Downloaded: {artifact.downloaded_at}")
    print()
    print("Use offline mode:")
    print(f"  GEO_MCP_OFFLINE=true GEO_MCP_PBF_PATH={artifact.path} uv run geo-mcp")
    if not store.has_osmium():
        print(
            "\nWarning: osmium CLI not found — install with "
            "`brew install osmium-tool` to clip bboxes from this PBF.",
            file=sys.stderr,
        )


def _cmd_status(_args: argparse.Namespace) -> None:
    settings = get_settings()
    store = PbfStore(settings)
    pbf = store.resolve_pbf()
    print(f"data_dir: {settings.data_dir}")
    print(f"cache_dir: {settings.cache_dir}")
    print(f"pbf_dir: {settings.pbf_dir}")
    print(f"offline: {settings.offline}")
    print(f"prefer_pbf: {settings.prefer_pbf}")
    print(f"osmium: {'yes' if store.has_osmium() else 'no'}")
    if pbf:
        print(f"pbf: {pbf} ({pbf.stat().st_size/1e6:.1f} MB)")
    else:
        print("pbf: (none — run geo-mcp ingest)")
    print("regions:")
    for name, url in sorted(GEOFABRIK_REGIONS.items()):
        mark = "*" if name == settings.pbf_region else " "
        print(f"  {mark} {name}: {url}")


def main(argv: list[str] | None = None) -> None:
    raw = list(sys.argv[1:] if argv is None else argv)
    commands = {"serve", "ingest", "status"}
    # Backward compatible: `geo-mcp` / `geo-mcp --transport stdio` → serve
    if not raw or raw[0] not in commands | {"-h", "--help"}:
        raw = ["serve", *raw]

    # Allow --log-level anywhere by peeling it off first.
    log_level = "WARNING"
    cleaned: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == "--log-level" and i + 1 < len(raw):
            log_level = raw[i + 1]
            i += 2
            continue
        if raw[i].startswith("--log-level="):
            log_level = raw[i].split("=", 1)[1]
            i += 1
            continue
        cleaned.append(raw[i])
        i += 1
    raw = cleaned

    parser = argparse.ArgumentParser(
        prog="geo-mcp",
        description="OSM multimodal MCP server (online Overpass or local PBF)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run MCP server (default)")
    serve.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_cmd_serve)

    ingest = sub.add_parser(
        "ingest",
        help="Download a Geofabrik OSM extract for offline routing",
    )
    ingest.add_argument(
        "--region",
        default="ile-de-france",
        choices=sorted(GEOFABRIK_REGIONS),
        help="Named Geofabrik region (default: ile-de-france)",
    )
    ingest.add_argument("--url", help="Custom .osm.pbf URL (overrides --region URL)")
    ingest.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a local PBF already exists",
    )
    ingest.set_defaults(func=_cmd_ingest)

    status = sub.add_parser("status", help="Show local data / offline status")
    status.set_defaults(func=_cmd_status)

    args = parser.parse_args(raw)
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args.func(args)


if __name__ == "__main__":
    main()
