# geo-mcp

OSM-backed multimodal (walk + transit) navigation MCP server. Gives LLM apps structured routing and geo tools for any distance — neighborhood walks to cross-city transit — without proprietary map APIs.

## Features

- **Offline / local PBF**: download a Geofabrik extract once (`geo-mcp ingest`), then route without Overpass
- **Distance-adaptive routing**: local multimodal A* for short trips; hierarchical transit-skeleton + walk access/egress for long ones
- **Tiled parallel loads**: walk/transit graphs from local clips or Overpass tiles
- **Deferred jobs + progress**: long routes can return `job_id`; poll with `get_job`
- **Resilient OSM I/O**: retries, circuit breakers, deadlines (online mode)
- **Transports**: stdio (default) and optional Streamable HTTP

## Tools

| Tool | Purpose |
|------|---------|
| `geocode` / `reverse_geocode` | Place ↔ coordinates |
| `plan_route` | Adaptive multimodal route (optional `defer`, `force_strategy`) |
| `submit_route` | Always-background route job |
| `get_job` / `cancel_job` / `list_jobs` | Job lifecycle |
| `load_area` | Prefetch tiles for explore/`describe_area` (optional for routing) |
| `find_nearby` | Nearby OSM POIs |
| `list_transit_options` | Nearby stops and lines |
| `describe_area` | Major roads, neighborhoods, landmarks |
| `snap_to_network` | Nearest graph node (auto-loads a local tile if needed) |

## Requirements

- Python **3.14+**
- [uv](https://github.com/astral-sh/uv)
- For offline clipping: [osmium-tool](https://osmcode.org/osmium-tool/) (`brew install osmium-tool`)

## Install

```bash
cd /path/to/geo-mcp
uv sync
brew install osmium-tool   # once, for offline bbox extracts
```

## Offline mode (recommended)

Download once, then never hit Overpass for routing:

```bash
# ~300–400 MB for Île-de-france
uv run geo-mcp ingest --region ile-de-france

uv run geo-mcp status

# Serve MCP offline
GEO_MCP_OFFLINE=true uv run geo-mcp
```

Cursor env example: set `GEO_MCP_OFFLINE=true` and `GEO_MCP_PBF_REGION=ile-de-france`. Other regions: `berlin`, `greater-london`, `france`. Or pass `--url` for any `.osm.pbf`.

## Run

```bash
# Cursor / Claude Desktop (stdio)
uv run geo-mcp

# Optional Streamable HTTP
uv run geo-mcp --transport streamable-http --host 127.0.0.1 --port 8000

# Verbose logs
uv run geo-mcp --log-level INFO
```

## Cursor config

```json
{
  "mcpServers": {
    "geo-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/geo-mcp",
        "run",
        "geo-mcp"
      ],
      "env": {
        "PYTHONWARNINGS": "ignore"
      }
    }
  }
}
```

## Suggested agent flow

1. `geocode` origin and destination
2. `plan_route` with coordinates (no prior `load_area` required)
3. If the response has `job_id`, poll `get_job` until `status` is `completed` (or `failed`)
4. Optionally `describe_area` / `find_nearby` after a local `load_area`

## Tests

```bash
uv run pytest
```

Live OSM smoke (needs network):

```bash
uv run python scripts/smoke_route.py
```

## Configuration

Environment variables use the `GEO_MCP_` prefix. Important knobs:

| Variable | Default | Meaning |
|----------|---------|---------|
| `GEO_MCP_OFFLINE` | `false` | Never call Overpass; require local PBF/cache |
| `GEO_MCP_PBF_REGION` | `ile-de-france` | Geofabrik region for ingest |
| `GEO_MCP_PBF_PATH` | — | Explicit path to `.osm.pbf` / `.osm` |
| `GEO_MCP_PREFER_PBF` | `true` | Use local PBF when present |
| `GEO_MCP_LOCAL_MAX_M` | `2000` | Below this → local A*; above → hierarchical |
| `GEO_MCP_ACCESS_RADIUS_M` | `800` | Access/egress stop search radius |
| `GEO_MCP_TILE_SIZE_DEG` | `0.01` | Walk tile grid size (~1 km) |
| `GEO_MCP_MAX_WORKERS` | `8` | Parallel tile workers |
| `GEO_MCP_SYNC_BUDGET_S` | `25` | Soft sync budget (long trips auto-defer) |
| `GEO_MCP_OPERATION_DEADLINE_S` | `120` | Hard deadline per route/load |
| `GEO_MCP_OVERPASS_RETRIES` | `2` | Retries per Overpass endpoint |
| `GEO_MCP_CACHE_DIR` | `~/.cache/geo-mcp/graphs` | Disk graph cache |

## Architecture

```
src/geo_mcp/
  domain/             # geo, network, pathfinding, hierarchical router
  application/        # use cases, job service, DTOs
  infrastructure/
    resilience/       # retry, circuit breaker, deadline
    graph/            # disk cache + tile store
    osm/              # Nominatim, Overpass, OSMnx repository
  interfaces/mcp/     # FastMCP tools (+ progress)
```

## Limits

- Transit uses OSM topology and heuristic speeds (not GTFS realtime schedules)
- Hierarchical routing stitches transit with walk tiles / geodesic walk fallbacks at the ends
- First fetch for uncached tiles still depends on Overpass/OSMnx latency

## Attribution & policy

- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
- Respect [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) and Overpass etiquette
