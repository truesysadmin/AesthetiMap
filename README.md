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
| `OVERPASS_URLS` | `https://overpass-api.de/api,https://overpass.private.coffee/api` | Comma-separated Overpass endpoint URLs. |
| `OSM_REQUEST_TIMEOUT` | `60` | OpenStreetMap request timeout in seconds. |
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

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/). Keep the appropriate map attribution when sharing generated posters.
