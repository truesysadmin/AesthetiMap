from pathlib import Path
from unittest.mock import Mock
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import networkx as nx
import pytest
from shapely.geometry import LineString, Point

import renderer


@pytest.mark.parametrize("lat,lon,ns,ew", [(12, 34, "N", "E"), (-12, 34, "S", "E"), (12, -34, "N", "W"), (-12, -34, "S", "W")])
def test_coordinates_quadrants(lat, lon, ns, ew):
    text = renderer.format_coordinates(lat, lon)
    assert ns in text and ew in text
    assert "-" not in text
    assert "12" in text and "34" in text


def test_filename_traversal_and_uniqueness():
    first = Path(renderer.generate_output_filename("../../a city", "terracotta", "png"))
    second = Path(renderer.generate_output_filename("../../a city", "terracotta", "png"))
    assert first.parent.resolve() == Path(renderer.POSTERS_DIR).resolve()
    assert first != second
    assert first.name.startswith("a_city_")
    assert Path(renderer.generate_output_filename("...", "terracotta", "png")).name.startswith("poster_")


@pytest.mark.parametrize("theme", ["../terracotta", "/terracotta", "Upper", "missing-theme"])
def test_invalid_theme(theme):
    with pytest.raises(ValueError):
        renderer.load_theme(theme)


def test_filename_rejects_theme_traversal():
    with pytest.raises(ValueError):
        renderer.generate_output_filename("Kyiv", "../terracotta", "png")


def test_split_layers_matches_values():
    frame = gpd.GeoDataFrame({
        "natural": ["water", "bay", "strait", "wood", None, None, None, None, None, None],
        "waterway": [None, None, None, None, "riverbank", "stream", None, None, None, None],
        "leisure": [None] * 6 + ["park", "pitch", None, None],
        "landuse": [None] * 8 + ["grass", "industrial"],
        "building": [None] * 9 + ["yes"],
        "geometry": [Point(0, 0)] * 10,
    }, crs="EPSG:4326")
    water, parks, buildings = renderer.split_layers(frame, True)
    assert list(water.index) == [0, 1, 2, 4]
    assert list(parks.index) == [6, 8]
    assert list(buildings.index) == [9]
    assert renderer.split_layers(frame, False)[2].empty


def test_cache_atomic_and_corrupt(monkeypatch):
    real_replace = renderer.os.replace
    replacements = []

    def replace(source, target):
        assert Path(source).parent == Path(target).parent
        assert Path(source).is_file()
        replacements.append((source, target))
        return real_replace(source, target)

    monkeypatch.setattr(renderer.os, "replace", replace)
    renderer.cache_set("test", {"value": 3})
    assert renderer.cache_get("test") == {"value": 3}
    assert replacements
    path = Path(renderer._cache_path("test"))
    path.write_bytes(b"broken pickle")
    assert renderer.cache_get("test") is None
    assert not path.exists()


def test_cache_failed_write_removes_temp(monkeypatch):
    def fail(*args):
        raise OSError("write failed")

    monkeypatch.setattr(renderer.os, "replace", fail)
    try:
        renderer.cache_set("failed", 1)
    except (OSError, renderer.CacheError):
        pass
    assert list(Path(renderer.CACHE_DIR).iterdir()) == []


def test_graph_endpoint_fallback(monkeypatch):
    original = renderer.ox.settings.overpass_url
    attempts = []
    sentinel = synthetic_graph()

    def download(*args, **kwargs):
        attempts.append(renderer.ox.settings.overpass_url)
        if len(attempts) == 1:
            raise RuntimeError("first endpoint unavailable")
        return sentinel

    monkeypatch.setattr(renderer.ox, "graph_from_bbox", download)
    assert renderer.fetch_graph((0, 0, 1, 1)) is sentinel
    assert len(attempts) == 2
    assert attempts[0] != attempts[1]
    assert renderer.ox.settings.overpass_url == original


def test_overpass_busy_is_bounded_and_restored(monkeypatch):
    from osmnx import _overpass

    original_time = _overpass.time
    original_url = renderer.ox.settings.overpass_url
    attempts = []

    def busy(*args, **kwargs):
        attempts.append(renderer.ox.settings.overpass_url)
        _overpass.time.sleep(55)
        pytest.fail("the busy shim did not interrupt the recursive retry")

    monkeypatch.setattr(renderer.ox, "graph_from_bbox", busy)
    monkeypatch.setattr(renderer, "_cancellable_wait", lambda *args: None)
    with pytest.raises(renderer.MapDataUnavailable):
        renderer.fetch_graph((0, 0, 1, 1))
    assert len(attempts) == 4
    assert _overpass.time is original_time
    assert renderer.ox.settings.overpass_url == original_url


def test_insufficient_data_not_retried(monkeypatch):
    from osmnx._errors import InsufficientResponseError

    download = Mock(side_effect=InsufficientResponseError("no data"))
    monkeypatch.setattr(renderer.ox, "features_from_bbox", download)
    assert renderer.fetch_features((0, 0, 1, 1), {"natural": "water"}, "water").empty
    assert download.call_count == 1
    monkeypatch.setattr(renderer.ox, "graph_from_bbox", download)
    with pytest.raises(renderer.NoMapData):
        renderer.fetch_graph((0, 0, 1, 1))
    assert download.call_count == 2


def test_cancel_propagates(monkeypatch):
    monkeypatch.setattr(renderer.ox, "graph_from_bbox", Mock(side_effect=renderer.GenerationCancelled()))
    with pytest.raises(renderer.GenerationCancelled):
        renderer.fetch_graph((0, 0, 1, 1))


def synthetic_graph():
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    graph.add_node(1, x=-34.001, y=-12.001)
    graph.add_node(2, x=-33.999, y=-11.999)
    graph.add_edge(1, 2, length=310, highway="residential", geometry=LineString([(-34.001, -12.001), (-33.999, -11.999)]))
    return graph


@pytest.fixture
def fake_map(monkeypatch):
    monkeypatch.setattr(renderer, "fetch_graph", lambda *a, **kw: synthetic_graph())
    monkeypatch.setattr(renderer, "fetch_features", lambda *a, **kw: gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"))
    return renderer.load_theme("terracotta")


def test_real_small_render_texts(tmp_path, monkeypatch, fake_map):
    texts = []
    from matplotlib.axes import Axes
    original_text = Axes.text

    def record(self, x, y, text, *args, **kwargs):
        texts.append((text, kwargs))
        return original_text(self, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Axes, "text", record)
    output = tmp_path / "poster.svg"
    renderer.create_poster("Test", "Country", (-12, -34), 1000, str(output), "svg", theme=fake_map, width=2, height=2)
    assert output.stat().st_size > 0
    assert any(renderer.format_coordinates(-12, -34) == text for text, _ in texts)
    assert any(text == "© OpenStreetMap contributors · aesthetimap.rastiegaiev.com" and kwargs["alpha"] == 0.7 for text, kwargs in texts)
    assert not plt.get_fignums()


def test_figure_closed_on_exception(monkeypatch, tmp_path, fake_map):
    def fail(*args, **kwargs):
        raise RuntimeError("projection failed")

    monkeypatch.setattr(renderer.ox, "project_graph", fail)
    with pytest.raises(RuntimeError, match="projection failed"):
        renderer.create_poster("Test", "Country", (-12, -34), 1000, str(tmp_path / "failed.svg"), "svg", theme=fake_map)
    assert not plt.get_fignums()


def test_cli_main_passes_theme_dictionary(monkeypatch):
    create = Mock()
    monkeypatch.setattr(renderer, "create_poster", create)
    monkeypatch.setattr(renderer, "get_coordinates", lambda *args, **kwargs: (50, 30))
    monkeypatch.setattr(sys, "argv", ["renderer.py", "--city", "Kyiv", "--country", "Ukraine", "--show-heart"])
    renderer.main()
    assert create.call_count == 1
    assert isinstance(create.call_args.kwargs["theme"], dict)
    assert create.call_args.kwargs["show_heart"] is True
    assert create.call_args.kwargs["width"] == 12
    assert create.call_args.kwargs["height"] == 16


@pytest.mark.parametrize("exception,expected", [
    (None, "CityNotFound"), (RuntimeError("service failed"), "MapDataUnavailable"),
    (renderer.GenerationCancelled(), "GenerationCancelled"),
])
def test_geocoding_errors(monkeypatch, exception, expected):
    locator = Mock()
    if exception is not None:
        locator.geocode.side_effect = exception
    else:
        locator.geocode.return_value = None
    monkeypatch.setattr(renderer, "Nominatim", Mock(return_value=locator))
    monkeypatch.setattr(renderer, "_cancellable_wait", lambda *args: None)
    with pytest.raises(getattr(renderer, expected)):
        renderer.get_coordinates("Kyiv", "Ukraine")


def test_feature_cache_key_depends_on_sorted_tags(monkeypatch):
    download = Mock(return_value=gpd.GeoDataFrame())
    monkeypatch.setattr(renderer.ox, "features_from_bbox", download)
    renderer.fetch_features((0, 0, 1, 1), {"natural": "water", "leisure": "park"}, "combined")
    renderer.fetch_features((0, 0, 1, 1), {"leisure": "park", "natural": "water"}, "combined")
    assert download.call_count == 1
    renderer.fetch_features((0, 0, 1, 1), {"natural": "water"}, "combined")
    assert download.call_count == 2


def test_geocoder_cache_case_normalized_and_hashed(monkeypatch):
    locator = Mock()
    locator.geocode.return_value.latitude = 50
    locator.geocode.return_value.longitude = 30
    monkeypatch.setattr(renderer, "Nominatim", Mock(return_value=locator))
    monkeypatch.setattr(renderer, "_cancellable_wait", lambda *args: None)
    assert renderer.get_coordinates("Kyiv", "Ukraine") == (50, 30)
    assert renderer.get_coordinates("KYIV", "UKRAINE") == (50, 30)
    assert locator.geocode.call_count == 1
    assert all("kyiv" not in path.name.lower() for path in Path(renderer.CACHE_DIR).iterdir())
