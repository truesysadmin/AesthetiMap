"""Synthetic vector tiles exercise geometry, provider limits, and drawing offline."""
import math
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import mapbox_vector_tile
import matplotlib.pyplot as plt
import numpy as np
import pytest
import requests
from PIL import Image
from pyproj import Geod, Transformer
from shapely.geometry import LineString, Polygon, box, mapping

import renderer
import tiles

TEMPLATE = "https://example.test/{z}/{x}/{y}.pbf"


def encode(layers):
    return mapbox_vector_tile.encode([
        {"name": name, "features": [{"geometry": mapping(geometry), "properties": props} for geometry, props in features]}
        for name, features in layers.items()
    ], default_options={"y_coord_down": True})


def road_tile():
    return encode({"transportation": [(LineString([(0, 2048), (4096, 2048)]), {"class": "minor"})]})


def response(status=200, data=b"tile", headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.iter_content = Mock(side_effect=lambda size: iter([data]))
    return result


def serve(monkeypatch, responses):
    get = Mock(side_effect=responses)
    monkeypatch.setattr(requests.Session, "get", get)
    return get


def load_synthetic(monkeypatch, data, coordinates=(14, 8192, 8192), **kwargs):
    monkeypatch.setattr(tiles, "get_tilejson", lambda *a: {"tiles": [TEMPLATE]})
    monkeypatch.setattr(tiles, "download_tiles", lambda *a: [(coordinates, data)])
    return tiles.load_layers((0, 0), 1000, 1000, **kwargs)


@pytest.mark.parametrize("latitude", [0, 43, 70])
def test_zoom_50km(latitude):
    bbox = tiles.bounding_box((latitude, 0), 25000, 25000)
    zoom = tiles.choose_zoom(bbox, 18)
    assert 8 <= zoom <= 14
    assert len(tiles.tile_range(bbox, zoom)) <= (200 if zoom == 14 else 500)
    if zoom < 14:
        assert len(tiles.tile_range(bbox, zoom + 1)) > (200 if zoom + 1 == 14 else 500)
    assert tiles.choose_zoom(bbox, 10) <= 10


def test_tile_range_wrap_and_clamp():
    assert tiles.tile_range((179, -1, -179, 1), 8) == tiles.tile_range((179, -1, 181, 1), 8)
    coordinates = tiles.tile_range((179, -90, 181, 90), 8)
    assert {x for _, x, _ in coordinates} == {255, 256}
    assert min(y for _, _, y in coordinates) == 0
    assert max(y for _, _, y in coordinates) == 255
    assert '/0/' in str(tiles.tile_path(TEMPLATE, (8, 256, 2)))


@pytest.mark.parametrize("point", [(43, 7), (70, 20), (0, 179.99)])
def test_coordinate_accuracy_and_directions(point):
    lat, lon = point
    target_lon, target_lat = lon + 0.01, lat + 0.01
    z = 14
    world_x = (target_lon + 180) / 360 * 2 ** z
    world_y = (1 - math.asinh(math.tan(math.radians(target_lat))) / math.pi) / 2 * 2 ** z
    x, y = math.floor(world_x), math.floor(world_y)
    geometry = LineString([((world_x - x) * 4096, (world_y - y) * 4096), (0, 0)])
    transformer = Transformer.from_crs('EPSG:4326', tiles.local_crs(point), always_xy=True)
    result = tiles.transform_layer([geometry], (z, x, y), 4096, transformer)[0]
    px, py = result.coords[0]
    expected = transformer.transform(target_lon, target_lat)
    assert np.linalg.norm(np.array([px, py]) - expected) < 1
    assert px > 0 and py > 0
    _, _, distance = Geod(ellps='WGS84').inv(lon, lat, target_lon, target_lat)
    assert abs(math.hypot(px, py) - distance) < 1


def test_tile_seams():
    transformer = Transformer.from_crs('EPSG:4326', tiles.local_crs((43, 7)), always_xy=True)
    left = tiles.transform_layer([LineString([(4000, 1000), (4096, 1200)])], (14, 8510, 6000), 4096, transformer)[0]
    right = tiles.transform_layer([LineString([(0, 1200), (100, 1400)])], (14, 8511, 6000), 4096, transformer)[0]
    assert np.linalg.norm(np.array(left.coords[-1]) - right.coords[0]) < 1


@pytest.mark.parametrize("cls,expected", list(tiles.ROAD_CLASSES.items()) + [(value, None) for value in (
    'rail', 'transit', 'ferry', 'aerialway', 'pier', 'bridge', 'raceway', 'motorway_construction', 'residential', 'unknown')])
def test_road_classes(monkeypatch, cls, expected):
    data = encode({'transportation': [(LineString([(0, 0), (4096, 4096)]), {'class': cls})]})
    if expected is None:
        with pytest.raises(tiles.NoMapData):
            load_synthetic(monkeypatch, data)
    else:
        layers = load_synthetic(monkeypatch, data)
        assert len(layers.roads[expected]) == 1
        assert sum(map(len, layers.roads.values())) == 1


def test_filters_and_clipped_shadows(monkeypatch):
    polygon = box(-100, -100, 4200, 4200)
    water_props = [{'class': 'lake'}, {'class': 'swimming_pool'}, {'class': 'lake', 'brunnel': 'tunnel'},
                   {'class': 'lake', 'intermittent': 1}]
    data = encode({
        'transportation': [(LineString([(0, 0), (4096, 4096)]), {'class': 'minor'})],
        'water': [(polygon, prop) for prop in water_props],
        'waterway': [(LineString([(0, 0), (4096, 4096)]), prop) for prop in [
            {'class': 'river'}, {'class': 'canal'}, {'class': 'stream'}, {'class': 'ditch'}, {'class': 'drain'},
            {'class': 'river', 'brunnel': 'tunnel'}, {'class': 'river', 'intermittent': 1}]],
        'landcover': [(polygon, {'class': cls, 'subclass': sub}) for cls, sub in [
            ('grass', 'park'), ('grass', 'grass'), ('grass', 'village_green'), ('grass', 'recreation_ground'),
            ('grass', 'meadow'), ('wood', 'park')]],
        'building': [(polygon, {'hide_3d': True})],
    })
    layers = load_synthetic(monkeypatch, data, show_buildings=True)
    assert len(layers.water) == 1
    assert len(layers.parks) == 4
    assert all(len(lines) == 1 for lines in layers.waterways.values())
    assert len(layers.buildings) == len(layers.building_shadows) == 1
    assert layers.building_shadows[0].area < layers.buildings[0].area
    assert not load_synthetic(monkeypatch, data).buildings


def test_invalid_polygon_repaired(monkeypatch):
    data = encode({'transportation': [(LineString([(0, 0), (4096, 4096)]), {'class': 'minor'})],
                   'water': [(Polygon([(0, 0), (4096, 4096), (0, 4096), (4096, 0), (0, 0)]), {'class': 'lake'})]})
    layers = load_synthetic(monkeypatch, data)
    assert all(polygon.is_valid for polygon in layers.water)


@pytest.mark.parametrize('status,cached', [(200, True), (204, True), (404, False)])
def test_empty_tile_cache(monkeypatch, status, cached):
    get = serve(monkeypatch, [response(status, b'')])
    assert tiles.download_tiles(TEMPLATE, [(8, 1, 1)])[0][1] == b''
    assert tiles.tile_path(TEMPLATE, (8, 1, 1)).exists() == cached
    if cached:
        tiles.download_tiles(TEMPLATE, [(8, 1, 1)])
        assert get.call_count == 1


def test_cache_hit_no_request(monkeypatch):
    path = tiles.tile_path(TEMPLATE, (8, 1, 1))
    tiles.atomic_write(path, road_tile())
    get = serve(monkeypatch, [])
    assert tiles.download_tiles(TEMPLATE, [(8, 1, 1)])[0][1] == road_tile()
    get.assert_not_called()


@pytest.mark.parametrize('first', [response(503), response(429, headers={'Retry-After': '25'}), requests.ConnectionError()])
def test_retry_success(monkeypatch, first):
    get = serve(monkeypatch, [first, response()])
    wait = Mock()
    monkeypatch.setattr(tiles, 'wait', wait)
    assert tiles.download_tiles(TEMPLATE, [(8, 1, 1)])[0][1] == b'tile'
    assert get.call_count == 2
    assert wait.call_args.args[0] == (10 if getattr(first, 'status_code', 0) == 429 else 1)
    assert get.call_args.kwargs['timeout'] == (5, 20)


def test_retry_three_attempts(monkeypatch):
    get = serve(monkeypatch, [response(500), response(500), response(500)])
    waits = Mock()
    monkeypatch.setattr(tiles, 'wait', waits)
    with pytest.raises(tiles.MapDataUnavailable):
        tiles.download_tiles(TEMPLATE, [(8, 1, 1)])
    assert get.call_count == 3
    assert [call.args[0] for call in waits.call_args_list] == [1, 3]


def test_client_error_no_retry(monkeypatch):
    get = serve(monkeypatch, [response(403)])
    with pytest.raises(tiles.MapDataUnavailable):
        tiles.download_tiles(TEMPLATE, [(8, 1, 1)])
    assert get.call_count == 1


def test_tile_size_limit(monkeypatch):
    serve(monkeypatch, [response(200, b'x' * (8 * 1024 * 1024 + 1))])
    with pytest.raises(tiles.MapDataUnavailable, match='too large'):
        tiles.download_tiles(TEMPLATE, [(8, 1, 1)])
    assert not tiles.tile_path(TEMPLATE, (8, 1, 1)).exists()


def test_byte_limit_restart(monkeypatch):
    monkeypatch.setattr(tiles, 'get_tilejson', lambda *a: {'tiles': [TEMPLATE]})
    monkeypatch.setattr(tiles, 'choose_zoom', lambda *a: 10)
    zooms = []

    def download(template, coordinates, *args):
        zooms.append(coordinates[0][0])
        if coordinates[0][0] > 8:
            budget = tiles._Budget()
            budget.add(35 * 1024 * 1024 + 1)
        return [(coordinates[0], road_tile())]

    monkeypatch.setattr(tiles, 'download_tiles', download)
    assert tiles.load_layers((0, 0), 100000, 100000)
    assert zooms == [10, 9, 8]
    monkeypatch.setattr(tiles, 'download_tiles', Mock(side_effect=tiles._TooMuchData()))
    with pytest.raises(tiles.MapDataUnavailable):
        tiles.load_layers((0, 0), 100000, 100000)


def test_hard_job_limits():
    budget = tiles._Budget()
    with pytest.raises(tiles.MapDataUnavailable):
        budget.add(46 * 1024 * 1024, network=True)
    budget.requests = 1000
    with pytest.raises(tiles.MapDataUnavailable):
        budget.request()


@pytest.mark.parametrize('cancelled', [False, True])
def test_parallel_limit_and_shutdown(monkeypatch, cancelled):
    active = 0
    maximum = 0
    lock = threading.Lock()
    cancel = threading.Event()

    def get(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(active, maximum)
        time.sleep(0.02)
        if cancelled:
            cancel.set()
        with lock:
            active -= 1
        return response()

    monkeypatch.setattr(requests.Session, 'get', get)
    if cancelled:
        with pytest.raises(tiles.GenerationCancelled):
            tiles.download_tiles(TEMPLATE, [(8, i, 0) for i in range(20)], cancel=cancel.is_set)
    else:
        tiles.download_tiles(TEMPLATE, [(8, i, 0) for i in range(20)])
    assert 1 < maximum <= 4
    assert active == 0
    assert not any(thread.name.startswith('map-tiles') for thread in threading.enumerate())


def test_tilejson_stale_and_validation(monkeypatch):
    import os
    get = serve(monkeypatch, [])
    info = {'tiles': [TEMPLATE], 'maxzoom': 14}
    result = response()
    result.json.return_value = info
    get.side_effect = [result]
    assert tiles.get_tilejson() == info
    assert tiles.get_tilejson() == info
    assert get.call_count == 1
    path = next((Path(tiles.CACHE_DIR) / 'tilejson').iterdir())
    os.utime(path, (0, 0))
    get.side_effect = requests.ConnectionError()
    assert tiles.get_tilejson() == info
    path.unlink()
    with pytest.raises(tiles.MapDataUnavailable):
        tiles.get_tilejson()


@pytest.mark.parametrize('template', ['https://example.test/tile', 'file:///{z}/{x}/{y}', 'http://example.test/{z}/{x}/{y}', 'https:///{z}/{x}/{y}'])
def test_invalid_template(template):
    with pytest.raises(tiles.MapDataUnavailable):
        tiles._validate_tilejson({'tiles': [template]})


def test_http_self_host(monkeypatch):
    monkeypatch.setattr(tiles, 'TILES_URL', 'http://localhost/tiles.json')
    assert tiles._validate_tilejson({'tiles': ['http://localhost/{z}/{x}/{y}']})


def test_corrupt_tile_deleted(monkeypatch):
    coordinate = (14, 8192, 8192)
    path = tiles.tile_path(TEMPLATE, coordinate)
    tiles.atomic_write(path, b'corrupt')
    with pytest.raises(tiles.MapDataUnavailable):
        load_synthetic(monkeypatch, b'corrupt', coordinate)
    assert not path.exists()


@pytest.mark.parametrize('statuses,error', [([404] * 3, tiles.MapDataUnavailable), ([404, 404, 200], None)])
def test_all_missing_tiles_mean_provider_error(monkeypatch, statuses, error):
    monkeypatch.setattr(tiles, 'get_tilejson', lambda *a: {'tiles': [TEMPLATE]})
    monkeypatch.setattr(tiles, 'tile_range', lambda *a: [(14, 8192, y) for y in (8191, 8192, 8193)])
    serve(monkeypatch, [response(status, road_tile()) for status in statuses])
    if error:
        with pytest.raises(error):
            tiles.load_layers((0, 0), 5000, 5000)
    else:
        assert tiles.load_layers((0, 0), 5000, 5000).roads['residential']


def test_no_roads(monkeypatch):
    with pytest.raises(tiles.NoMapData):
        load_synthetic(monkeypatch, encode({'water': [(box(0, 0, 1000, 1000), {'class': 'lake'})]}))


def test_road_outside_view_is_no_data(monkeypatch):
    # Tile 8192/8192 starts at 0,0; its far edge is about 2.4 km away, outside a 1 km view.
    far = encode({'transportation': [(LineString([(4000, 4000), (4096, 4090)]), {'class': 'minor'})]})
    with pytest.raises(tiles.NoMapData):
        load_synthetic(monkeypatch, far)


def test_holes_and_overlaps_pixels():
    fig, ax = plt.subplots(figsize=(2, 2), dpi=100)
    ax.set_position((0, 0, 1, 1))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis('off')
    polygon = Polygon([(1, 1), (9, 1), (9, 9), (1, 9)], [[(4, 4), (6, 4), (6, 6), (4, 6)]])
    renderer.draw_polygons(ax, [polygon, box(2, 2, 3, 3)], '#0000ff', 1)
    fig.canvas.draw()
    pixels = np.asarray(fig.canvas.buffer_rgba())
    assert tuple(pixels[100, 100, :3]) == (255, 255, 255)
    assert tuple(pixels[150, 50, :3]) == (0, 0, 255)
    assert tuple(pixels[40, 40, :3]) == (0, 0, 255)
    plt.close(fig)


def test_synthetic_end_to_end_png(monkeypatch, tmp_path):
    # A map centred in the tile: water occupies the west, a road crosses the centre.
    coordinate = (14, 8192, 8192)
    data = encode({'transportation': [(LineString([(0, 2048), (4096, 2048)]), {'class': 'motorway'})],
                   'water': [(box(0, 0, 2048, 4096), {'class': 'lake'})]})
    monkeypatch.setattr(tiles, 'get_tilejson', lambda *a: {'tiles': [TEMPLATE]})
    tiles.atomic_write(tiles.tile_path(TEMPLATE, coordinate), data)
    lat = math.degrees(math.atan(math.sinh(-math.pi / 2 ** 14)))
    point = (lat, 180 / 2 ** 14)
    theme = renderer.load_theme('terracotta')
    theme.update(water='#0000ff', road_motorway='#ff0000', bg='#ffffff')
    output = tmp_path / 'poster.png'
    progress = []
    renderer.create_poster('Test', 'Country', point, 2000, str(output), 'png', theme, width=2, height=2,
                           no_title=True, no_coords=True, callback=lambda message, percent: progress.append((message, percent)))
    pixels = np.array(Image.open(output))[:, :, :3]
    h, w = pixels.shape[:2]
    assert tuple(pixels[h // 3, w // 3]) == (0, 0, 255)
    assert tuple(pixels[h // 3, 2 * w // 3]) == (255, 255, 255)
    road_pixels = pixels[h // 2 - 5:h // 2 + 5, 2 * w // 3]
    assert np.any((road_pixels[:, 0] > 200) & (road_pixels[:, 1] < 100) & (road_pixels[:, 2] < 100))
    assert all(str(tmp_path) not in message for message, _ in progress)
    percents = [percent for _, percent in progress if percent is not None]
    assert percents == sorted(percents)


def test_antimeridian_geometry_continuous():
    point = (0, 180)
    transformer = Transformer.from_crs('EPSG:4326', tiles.local_crs(point), always_xy=True)
    left = tiles.transform_layer([LineString([(4000, 0), (4096, 0)])], (8, 255, 128), 4096, transformer)[0]
    right = tiles.transform_layer([LineString([(0, 0), (100, 0)])], (8, 256, 128), 4096, transformer)[0]
    assert np.linalg.norm(np.array(left.coords[-1]) - right.coords[0]) < 1
    assert left.coords[0][0] < 0 < right.coords[-1][0]
    bbox = tiles.bounding_box((0, 179.99), 25000, 25000)
    assert bbox[2] > 180
    assert bbox[2] - bbox[0] < 1


def test_cached_bytes_trigger_soft_limit(monkeypatch):
    monkeypatch.setattr(tiles, 'MAX_BYTES', 10)
    tiles.atomic_write(tiles.tile_path(TEMPLATE, (8, 1, 1)), b'123456')
    tiles.atomic_write(tiles.tile_path(TEMPLATE, (8, 2, 1)), b'123456')
    with pytest.raises(tiles._TooMuchData):
        tiles.download_tiles(TEMPLATE, [(8, 1, 1), (8, 2, 1)])


def test_retry_wait_cancellable(monkeypatch):
    serve(monkeypatch, [response(429, headers={'Retry-After': '10'})])
    cancel = threading.Event()
    original = tiles.wait

    def wait(seconds, cancel_check):
        cancel.set()
        original(seconds, cancel_check)

    monkeypatch.setattr(tiles, 'wait', wait)
    with pytest.raises(tiles.GenerationCancelled):
        tiles.download_tiles(TEMPLATE, [(8, 1, 1)], cancel=cancel.is_set)
    assert not any(thread.name.startswith('map-tiles') for thread in threading.enumerate())


def test_progress_bounded(monkeypatch):
    monkeypatch.setattr(requests.Session, 'get', lambda *a, **kw: response(204))
    updates = []
    tiles.download_tiles(TEMPLATE, [(8, x, 0) for x in range(35)], lambda message, percent: updates.append((message, percent)))
    assert 1 <= len(updates) <= 10
    assert updates[-1] == ('Downloading map tiles (35/35)...', 45)
    assert all(10 <= percent <= 45 for _, percent in updates)


def test_cancel_between_tiles(monkeypatch):
    monkeypatch.setattr(tiles, 'get_tilejson', lambda *a: {'tiles': [TEMPLATE]})
    monkeypatch.setattr(tiles, 'download_tiles', lambda *a: [((14, 8192, 8192), road_tile())] * 2)
    cancel = threading.Event()
    original = tiles._decode_layers

    def decode(*args):
        original(*args)
        cancel.set()

    monkeypatch.setattr(tiles, '_decode_layers', decode)
    with pytest.raises(tiles.GenerationCancelled):
        tiles.load_layers((0, 0), 1000, 1000, cancel=cancel.is_set)


def test_request_url_wraps_x(monkeypatch):
    get = serve(monkeypatch, [response()])
    assert tiles.download_tiles(TEMPLATE, [(8, 256, 100)]) == [((8, 256, 100), b'tile')]
    assert get.call_args.args[0] == 'https://example.test/8/0/100.pbf'


@pytest.mark.parametrize('lat,lon', [(0, 0), (43.25, -5.77), (-33.9, 151.2), (70, 25), (-16.8, 179.98)])
def test_bounding_box_covers_rectangle(lat, lon):
    west, south, east, north = tiles.bounding_box((lat, lon), 10000, 15000)
    inverse = Transformer.from_crs(tiles.local_crs((lat, lon)), 'EPSG:4326', always_xy=True)
    for x in np.linspace(-10000, 10000, 9):
        for y in np.linspace(-15000, 15000, 9):
            plon, plat = inverse.transform(x, y)
            plon = lon + (plon - lon + 180) % 360 - 180
            assert west <= plon <= east and south <= plat <= north
    assert Geod(ellps='WGS84').inv(west, lat, east, lat)[2] < 2 * 10000 * 1.1
