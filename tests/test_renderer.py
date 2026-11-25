from pathlib import Path
from unittest.mock import Mock
import sys

import matplotlib.pyplot as plt
import pytest
from shapely.geometry import LineString
import tiles

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
    path.write_bytes(b"broken JSON")
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


@pytest.fixture
def fake_map(monkeypatch):
    layers = tiles.MapLayers()
    layers.roads["residential"].append(LineString([(-100, -100), (100, 100)]))
    monkeypatch.setattr(tiles, "load_layers", lambda *a, **kw: layers)
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
    assert any(text == "© OpenMapTiles © OpenStreetMap contributors · aesthetimap.rastiegaiev.com" and kwargs["alpha"] == 0.7 for text, kwargs in texts)
    assert not plt.get_fignums()


def test_titles_are_literal_text(tmp_path, fake_map):
    output = tmp_path / "poster.png"
    renderer.create_poster(r"$\bad$", r"$\frac$", (1, 2), 1000, str(output), "png", theme=fake_map, width=2, height=2)
    assert output.stat().st_size > 0


def test_figure_closed_on_exception(monkeypatch, tmp_path, fake_map):
    def fail(*args, **kwargs):
        raise RuntimeError("drawing failed")

    monkeypatch.setattr(renderer, "draw_polygons", fail)
    with pytest.raises(RuntimeError, match="drawing failed"):
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


@pytest.mark.parametrize("cancelled", [False, True])
def test_partial_output_removed(monkeypatch, tmp_path, fake_map, cancelled):
    from matplotlib.figure import Figure
    output = tmp_path / "partial.png"

    def save(*args, **kwargs):
        output.write_bytes(b"partial")
        if not cancelled:
            raise OSError("disk full")

    monkeypatch.setattr(Figure, "savefig", save)
    with pytest.raises(renderer.GenerationCancelled if cancelled else OSError):
        renderer.create_poster("Test", "Country", (0, 0), 1000, str(output), "png", theme=fake_map,
                               cancel_check=lambda: output.exists() if cancelled else False)
    assert not output.exists()
    assert not plt.get_fignums()
