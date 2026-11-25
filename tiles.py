"""User-job-only OpenMapTiles retrieval and local metric geometry conversion."""
import hashlib
import json
import math
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import mapbox_vector_tile
import numpy as np
import requests
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Polygon, box, shape

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "cache"))
TILES_URL = os.environ.get("TILES_URL", "https://tiles.openfreemap.org/planet")
HTTP_USER_AGENT = os.environ.get("HTTP_USER_AGENT", "map-poster-service/1.0")
MAX_TILES = 200
# Coarser tiles are several times smaller, so more of them fit into the byte limit.
MAX_COARSE_TILES = 500
# Leaves room under the 45 MB job limit for a restart at a coarser zoom.
MAX_BYTES = 30 * 1024 * 1024
MAX_TILE_BYTES = 8 * 1024 * 1024
WORLD = 2 * math.pi * 6378137
ROAD_CLASSES = {"motorway": "motorway", "trunk": "primary", "primary": "primary", "secondary": "secondary",
                "tertiary": "tertiary", "minor": "residential", **dict.fromkeys(
                    ["service", "track", "path", "busway", "bus_guideway"], "default")}


class GenerationCancelled(Exception):
    """A job was cancelled."""


class MapDataUnavailable(Exception):
    """The map provider could not supply usable data."""


class CityNotFound(ValueError):
    """The geocoder found no matching city."""


class NoMapData(ValueError):
    """No roads exist in the requested area."""


class _TooMuchData(Exception):
    """Retry this job at a coarser zoom."""


def check_cancel(cancel=None):
    if cancel and cancel():
        raise GenerationCancelled()


def wait(seconds, cancel=None):
    deadline = time.monotonic() + seconds
    while True:
        check_cancel(cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def atomic_write(path, data):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
            temporary = target.name
            target.write(data)
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _validate_tilejson(value):
    try:
        template = value["tiles"][0]
        scheme = urlsplit(template).scheme
        if (scheme != "https" and not (scheme == "http" and urlsplit(TILES_URL).scheme == "http")) or not urlsplit(template).netloc:
            raise ValueError("Invalid tile URL")
        if not all(token in template for token in ("{z}", "{x}", "{y}")):
            raise ValueError("Missing tile coordinates")
        if type(value.get("maxzoom", 14)) is not int or value.get("maxzoom", 14) < 8:
            raise ValueError("Invalid maximum zoom")
        return value
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise MapDataUnavailable("Invalid TileJSON") from exc


def get_tilejson(cancel=None):
    check_cancel(cancel)
    path = Path(CACHE_DIR) / "tilejson" / (hashlib.sha256(TILES_URL.encode()).hexdigest() + ".json")
    stale = None
    try:
        stale = _validate_tilejson(json.loads(path.read_text()))
        if time.time() - path.stat().st_mtime < 3600:
            return stale
    except (OSError, ValueError, MapDataUnavailable):
        pass
    try:
        with requests.Session() as session:
            response = session.get(TILES_URL, timeout=15, headers={"User-Agent": HTTP_USER_AGENT})
            try:
                response.raise_for_status()
                value = _validate_tilejson(response.json())
            finally:
                response.close()
        check_cancel(cancel)
        atomic_write(path, json.dumps(value).encode())
        return value
    except GenerationCancelled:
        raise
    except (requests.RequestException, ValueError, MapDataUnavailable) as exc:
        check_cancel(cancel)
        if stale is not None:
            return stale
        raise MapDataUnavailable("TileJSON unavailable") from exc


def tile_range(bbox_lonlat, zoom):
    west, south, east, north = bbox_lonlat
    if east < west:
        east += 360
    n = 2 ** zoom

    def tile_y(lat):
        lat = math.radians(max(-85.05112878, min(85.05112878, lat)))
        return max(0, min(n - 1, math.floor((1 - math.asinh(math.tan(lat)) / math.pi) / 2 * n)))

    xs = range(math.floor((west + 180) / 360 * n), math.floor((east + 180) / 360 * n) + 1)
    ys = range(tile_y(north), tile_y(south) + 1)
    return [(zoom, x, y) for x in xs for y in ys]


def choose_zoom(bbox_lonlat, maxzoom):
    for zoom in range(min(14, maxzoom), 7, -1):
        if len(tile_range(bbox_lonlat, zoom)) <= (MAX_TILES if zoom == 14 else MAX_COARSE_TILES):
            return zoom
    return 8


def tile_path(template, tile):
    z, x, y = tile
    return Path(CACHE_DIR) / "tiles" / hashlib.sha256(template.encode()).hexdigest()[:12] / str(z) / str(x % (2 ** z)) / f"{y}.pbf"


def _retry_delay(response, attempt):
    delay = (1, 3)[attempt]
    value = response.headers.get("Retry-After")
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError):
                pass
    return max(0, min(10, delay))


class _Budget:
    def __init__(self):
        self.bytes = 0
        self.requests = 0
        self.missing = 0
        self.transferred = 0
        self.lock = threading.Lock()

    def add(self, count, network=False):
        with self.lock:
            if network:
                self.transferred += count
                if self.transferred > 45 * 1024 * 1024:
                    raise MapDataUnavailable("Job download limit exceeded")
            self.bytes += count
            if self.bytes > MAX_BYTES:
                raise _TooMuchData()

    def miss(self):
        with self.lock:
            self.missing += 1

    def request(self):
        with self.lock:
            self.requests += 1
            if self.requests > 1000:
                raise MapDataUnavailable("Tile request limit exceeded")


def _download_tile(session, template, tile, cancel, budget):
    path = tile_path(template, tile)
    check_cancel(cancel)
    try:
        data = path.read_bytes()
    except OSError:
        data = None
    if data is not None:
        if len(data) > MAX_TILE_BYTES:
            raise MapDataUnavailable("Tile too large")
        budget.add(len(data))
        return data
    z, x, y = tile
    url = template.replace("{z}", str(z)).replace("{x}", str(x % (2 ** z))).replace("{y}", str(y))
    for attempt in range(3):
        check_cancel(cancel)
        delay = (1, 3)[min(attempt, 1)]
        try:
            budget.request()
            with session.get(url, timeout=(5, 20), stream=True) as response:
                status = response.status_code
                if status == 404:
                    budget.miss()
                    return b""
                if status == 204:
                    atomic_write(path, b"")
                    return b""
                if status == 200:
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        check_cancel(cancel)
                        size += len(chunk)
                        if size > MAX_TILE_BYTES:
                            raise MapDataUnavailable("Tile too large")
                        budget.add(len(chunk), network=True)
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    atomic_write(path, data)
                    return data
                if status != 429 and status < 500:
                    raise MapDataUnavailable("Tile request rejected")
                if attempt < 2:
                    delay = _retry_delay(response, attempt)
        except requests.RequestException:
            pass
        if attempt < 2:
            wait(delay, cancel)
    raise MapDataUnavailable("Tile download failed")


def download_tiles(template, coordinates, callback=None, cancel=None, budget=None):
    if len(coordinates) > 1000:
        raise MapDataUnavailable("Too many tiles")
    budget = budget or _Budget()
    stopped = threading.Event()
    local = threading.local()
    sessions, lock = [], threading.Lock()

    def cancelled():
        return stopped.is_set() or (cancel and cancel())

    def fetch(tile):
        if not hasattr(local, "session"):
            local.session = requests.Session()
            local.session.headers.update({"User-Agent": HTTP_USER_AGENT})
            with lock:
                sessions.append(local.session)
        return _download_tile(local.session, template, tile, cancelled, budget)

    results = []
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="map-tiles")
    futures = {}
    try:
        check_cancel(cancel)
        futures = {executor.submit(fetch, tile): tile for tile in coordinates}
        step = max(1, math.ceil(len(coordinates) / 10))
        for completed, future in enumerate(as_completed(futures), 1):
            check_cancel(cancel)
            results.append((futures[future], future.result()))
            if callback and (completed % step == 0 or completed == len(coordinates)):
                callback(f"Downloading map tiles ({completed}/{len(coordinates)})...", 10 + int(35 * completed / len(coordinates)))
        return results
    finally:
        stopped.set()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        for session in sessions:
            session.close()


def local_crs(point):
    lat, lon = point
    return CRS.from_proj4(f"+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m")


def bounding_box(point, half_width_m, half_height_m):
    inverse = Transformer.from_crs(local_crs(point), "EPSG:4326", always_xy=True)
    w, h = half_width_m * 1.02, half_height_m * 1.02
    xs, ys = zip(*[(-w, -h), (0, -h), (w, -h), (w, 0), (w, h), (0, h), (-w, h), (-w, 0)])
    lons, lats = inverse.transform(xs, ys)
    lons = point[1] + (np.asarray(lons) - point[1] + 180) % 360 - 180
    return min(lons), min(lats), max(lons), max(lats)


def transform_layer(geometries, tile, extent, transformer):
    """Transform one layer's complete coordinate array in one projection call."""
    coordinates = shapely.get_coordinates(geometries)
    if not len(coordinates):
        return geometries
    z, x, y = tile
    mx = ((x + coordinates[:, 0] / extent) / 2 ** z - 0.5) * WORLD
    my = (0.5 - (y + coordinates[:, 1] / extent) / 2 ** z) * WORLD
    lons = mx / WORLD * 360
    lats = np.degrees(np.arctan(np.sinh(my / (WORLD / (2 * math.pi)))))
    px, py = transformer.transform(lons, lats)
    return shapely.set_coordinates(np.asarray(geometries, dtype=object).copy(), np.column_stack((px, py)))


def _parts(geometry, kind):
    if geometry.is_empty:
        return []
    if geometry.geom_type == kind:
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [part for child in geometry.geoms for part in _parts(child, kind)]
    return []


@dataclass
class MapLayers:
    roads: dict[str, list[LineString]] = field(default_factory=lambda: {
        key: [] for key in ("motorway", "primary", "secondary", "tertiary", "residential", "default")})
    water: list[Polygon] = field(default_factory=list)
    waterways: dict[str, list[LineString]] = field(default_factory=lambda: {key: [] for key in ("river", "canal", "stream")})
    parks: list[Polygon] = field(default_factory=list)
    buildings: list[Polygon] = field(default_factory=list)
    building_shadows: list[Polygon] = field(default_factory=list)


def _destination(layers, name, properties, show_buildings):
    cls = properties.get("class")
    if name == "transportation" and cls in ROAD_CLASSES:
        return layers.roads[ROAD_CLASSES[cls]], "LineString"
    if name in ("water", "waterway"):
        if properties.get("brunnel") == "tunnel" or properties.get("intermittent") in (1, "1"):
            return None
        if name == "water" and cls != "swimming_pool":
            return layers.water, "Polygon"
        if name == "waterway" and cls in layers.waterways:
            return layers.waterways[cls], "LineString"
    if name == "landcover" and cls == "grass" and properties.get("subclass") in {"park", "grass", "village_green", "recreation_ground"}:
        return layers.parks, "Polygon"
    if name == "building" and show_buildings:
        return layers.buildings, "Polygon"
    return None


def _decode_layers(layers, data, template, tile, transformer, show_buildings):
    if not data:
        return
    try:
        decoded = mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})
    except Exception as exc:
        try:
            tile_path(template, tile).unlink()
        except OSError:
            pass
        raise MapDataUnavailable("Invalid map tile") from exc
    for name, layer in decoded.items():
        geometries, destinations, shadows = [], [], []
        extent = layer.get("extent", 4096)
        for feature in layer["features"]:
            destination = _destination(layers, name, feature.get("properties", {}), show_buildings)
            if destination is None:
                continue
            geometry = shape(feature["geometry"])
            if not geometry.is_valid:
                geometry = shapely.make_valid(geometry)
            for part in _parts(geometry, destination[1]):
                geometries.append(part)
                destinations.append(destination[0])
                if name == "building":
                    shadows.extend(_parts(part.intersection(box(0, 0, extent, extent)), "Polygon"))
        for geometry, destination in zip(transform_layer(geometries, tile, extent, transformer), destinations):
            if not geometry.is_empty:
                destination.append(geometry)
        layers.building_shadows.extend(transform_layer(shadows, tile, extent, transformer))


def load_layers(point, half_width_m, half_height_m, callback=None, cancel=None, show_buildings=False):
    info = get_tilejson(cancel)
    template = info["tiles"][0]
    bbox = bounding_box(point, half_width_m, half_height_m)
    zoom = choose_zoom(bbox, info.get("maxzoom", 14))
    # Enforce hard transfer and request limits across every zoom attempt.
    budget = _Budget()
    progress = 10

    def report(message, percent):
        nonlocal progress
        progress = max(progress, percent)
        if callback:
            callback(message, progress)

    while True:
        try:
            # The soft byte limit applies to each selected set, including cache hits.
            budget.bytes = budget.missing = 0
            downloaded = download_tiles(template, tile_range(bbox, zoom), report, cancel, budget)
            break
        except _TooMuchData as exc:
            if zoom == 8:
                raise MapDataUnavailable("Map data exceeds job limit") from exc
            zoom -= 1
    if budget.missing == len(downloaded):
        raise MapDataUnavailable("The tile set is gone")
    layers = MapLayers()
    transformer = Transformer.from_crs("EPSG:4326", local_crs(point), always_xy=True)
    for tile, data in downloaded:
        check_cancel(cancel)
        _decode_layers(layers, data, template, tile, transformer, show_buildings)
    view = box(-half_width_m, -half_height_m, half_width_m, half_height_m)
    if not any(shapely.intersects(lines, view).any() for lines in layers.roads.values() if lines):
        raise NoMapData("No street network in this area")
    return layers
