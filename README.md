# geo-mcp

OSM-backed multimodal (walk + transit) navigation MCP server. Gives LLM apps a structured graph of the city so they can geocode, route, explore POIs, and reason about urban layout without proprietary map APIs.

## Features

- **Clean Architecture / DDD** (Python 3.14): domain pathfinding stays free of OSM/NetworkX details
- **Multimodal graph**: OSMnx walking network + OSM transit stops/routes + transfer edges
- **Disk cache**: bbox-keyed local graph cache under `~/.cache/geo-mcp/`
- **MCP tools** compatible with Cursor, Claude Desktop, Codex, etc. (stdio)

## Tools

| Tool | Purpose |
|------|---------|
| `load_area` | Fetch/cache walk+transit graph for a place or bbox |
| `geocode` | Place / address → coordinates |
| `reverse_geocode` | Coordinates → place |
| `plan_route` | Multimodal A* path with legs + narrative |
| `find_nearby` | Nearby OSM POIs |
| `list_transit_options` | Nearby stops and lines |
| `describe_area` | Major roads, neighborhoods, landmarks, connectivity notes |
| `snap_to_network` | Nearest graph node to a coordinate |

## Requirements

- Python **3.14+**
- [uv](https://github.com/astral-sh/uv)

## Install

```bash
cd /path/to/geo-mcp
uv sync
```

## Run (stdio MCP)

```bash
uv run geo-mcp
```

## Cursor config

Project example: [`.cursor/mcp.json`](.cursor/mcp.json)

Or add to `~/.cursor/mcp.json`:

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
      ]
    }
  }
}
```

## Suggested agent flow

1. `geocode` the destination (and origin if needed)
2. `load_area` for the neighborhood / place
3. `describe_area` for spatial context
4. `plan_route` with coordinates
5. Optionally `find_nearby` / `list_transit_options`

## Tests

```bash
uv run pytest
```

Live OSM smoke (needs network):

```bash
uv run python scripts/smoke_route.py
```

## Configuration

Environment variables use the `GEO_MCP_` prefix:

- `GEO_MCP_USER_AGENT` — Nominatim/Overpass User-Agent
- `GEO_MCP_CACHE_DIR` — graph cache directory
- `GEO_MCP_NOMINATIM_URL` / `GEO_MCP_OVERPASS_URL`

## Attribution & policy

- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
- Respect [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) and Overpass etiquette (polite User-Agent, modest request rates)

## Architecture

```
src/geo_mcp/
  domain/           # entities + pure pathfinding
  application/      # use cases + DTOs
  infrastructure/   # OSM adapters + cache
  interfaces/mcp/   # FastMCP tools
```

## MVP limits

- Transit uses OSM topology / heuristic speeds (not GTFS schedules)
- Large city-wide bboxes are automatically shrunk around the geocoded center
- First `load_area` for a bbox can take a while while Overpass/OSMnx respond
