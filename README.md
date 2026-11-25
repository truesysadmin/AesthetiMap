# AesthetiMap

Create customizable city map posters using OpenStreetMap data, curated themes, and PNG, SVG, or PDF exports. Adjust coverage up to 50,000 meters, print dimensions from 2 to 40 inches, title placement, gradients, building shadows, and topography.

![AesthetiMap example](docs/example.png)

## Run locally

Use Docker Compose for the frontend at http://localhost:8080 and the backend at http://localhost:8000:

```bash
mkdir -p cache posters
# On Linux, allow the backend container's UID/GID 1001 to write these bind mounts.
sudo chown -R 1001:1001 cache posters
docker compose -f docker-compose.dev.yml up -d --build
```

The production compose file expects an existing external Docker network named `contolstation` and an outer reverse proxy connected to it. Configure that proxy to forward to `frontend:80` and set `X-Real-IP` from its own remote address. It must overwrite any incoming client-supplied `X-Real-IP` header.

```bash
docker network create contolstation
docker compose up -d --build
```

Both configurations keep cached map data in `./cache` and generated posters in `./posters`. Posters expire through automatic cleanup; regenerate expired shared posters.

## CLI

Install Python dependencies and run the renderer:

```bash
pip install -r backend/requirements.txt -r requirements.txt
python renderer.py --city "Kyiv" --country "Ukraine" --theme gold_on_porcelain --span 20000
python renderer.py --help
```

## Configuration

Set these environment variables on the backend service in the compose file:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_CLEANUP_DAYS` | `7` | Maximum poster age in days. |
| `MAX_CLEANUP_MB` | `1024` | Maximum total poster storage in MB. |
| `CLEANUP_INTERVAL_HOURS` | `6` | Time between cleanup runs. |
| `MAX_SPAN_M` | `50000` | Maximum map coverage in meters. |
| `MAX_QUEUED` | `5` | Maximum queued generation jobs. |
| `JOB_TIMEOUT_SECONDS` | `600` | Generation job timeout in seconds. |
| `STUCK_GRACE_SECONDS` | `300` | Extra time after a timeout before a stuck process exits and Docker restarts it. |
| `TILES_URL` | `https://tiles.openfreemap.org/planet` | OpenMapTiles-schema TileJSON endpoint. |
| `HTTP_USER_AGENT` | `map-poster-service/1.0` | Identifying User-Agent sent to map services. |

## Checks

```bash
pip install -r backend/requirements.txt -r requirements.txt -r requirements-dev.txt
flake8 --max-line-length=160
pytest -q
cd frontend
npm ci
npm run build
```

## Credits and license

AesthetiMap is based on [maptoposter by Ankur Gupta](https://github.com/originalankur/maptoposter), with application development by Denys Rastiegaiev. The software is distributed under the [MIT License](LICENSE).

Map data © [OpenMapTiles](https://www.openmaptiles.org/) © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/). Keep the appropriate map attribution when sharing generated posters.

## Vector tile data

Map geometry comes from OpenMapTiles-schema vector tiles, supplied by [OpenFreeMap](https://openfreemap.org) by default. TileJSON is cached for one hour, with a stale fallback during provider outages. Tiles and geocoder JSON results are cached on disk.

Provider terms are hard limits: at most four concurrent requests, 1,000 tile requests and 45 MB per user job. The renderer targets at most 200 tiles and lowers zoom if a tile set exceeds 35 MB. Individual tiles are limited to 8 MB. Requests occur only during user jobs; there is no prefetching or background refresh.

For self-hosting, set `TILES_URL` to any TileJSON server using the OpenMapTiles schema, for example `go-pmtiles serve`. HTTPS tile templates are required unless the TileJSON endpoint itself uses HTTP. Preserve the generated attribution: “© OpenMapTiles © OpenStreetMap contributors · aesthetimap.rastiegaiev.com”.
