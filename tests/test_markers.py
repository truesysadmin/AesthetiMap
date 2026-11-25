"""Physical vector marker rendering and legacy renderer compatibility."""
import re
from pathlib import Path
from unittest.mock import Mock

import matplotlib.pyplot as plt
from matplotlib.path import Path as PlotPath
import numpy as np
import pytest

import markers
import renderer


THEME = {"text": "#111111", "bg": "#ffffff", "water": "#224488", "road_motorway": "#886622"}
CROP = ((-100, 100), (-100, 100))


def mark(kind="heart", **changes):
    return dict(type=kind, lat=None, lon=None, size=72, opacity=1, color="#ff0000", label="") | changes


def canvas(dpi=100):
    fig = plt.figure(figsize=(4, 4), dpi=dpi, facecolor="#ffffff")
    ax = fig.add_axes([0, 0, 1, 1], facecolor="#ffffff")
    ax.set(xlim=CROP[0], ylim=CROP[1])
    ax.axis("off")
    return fig, ax


def sample(fig, ax, x, y):
    pixels = np.asarray(fig.canvas.buffer_rgba())
    px, py = ax.transData.transform((x, y))
    return pixels[pixels.shape[0] - 1 - round(py), round(px), :3]


def test_sizes_match_the_browser():
    source = (Path(__file__).resolve().parents[1] / "frontend/markers.js").read_text()
    for name in ("LABEL_SIZE", "LINE_WIDTH"):
        assert float(re.search(rf"export const {name} = ([\d.]+);", source).group(1)) == getattr(markers, name)


def test_shapes_are_identical():
    root = Path(__file__).resolve().parents[1]
    assert (root / "marker_shapes.json").read_bytes() == (root / "frontend/marker-shapes.json").read_bytes()


def test_parser_flips_y_and_anchor_and_preserves_curve_and_holes():
    path = markers.parse_path("M10 20L20 20C20 10 10 10 10 20ZM12 18L12 16L14 18Z", (10, 20))
    assert tuple(path.vertices[0]) == (0, 0)
    assert tuple(path.vertices[2]) == (10, 10)
    assert list(path.codes).count(PlotPath.MOVETO) == 2
    assert list(path.codes).count(PlotPath.CURVE4) == 3
    assert list(path.codes).count(PlotPath.CLOSEPOLY) == 2
    assert markers.shape_path("pin") is markers.shape_path("pin")
    for kind in markers.SHAPES:
        assert markers.shape_path(kind).readonly


@pytest.mark.parametrize("data", ["", "M", "M0", "M0 0L", "M0 0C", "M0 0Q1 2 3 4", "L0 0Zx"])
def test_parser_rejects_invalid_data(data):
    with pytest.raises(ValueError):
        markers.parse_path(data)


def test_center_pixel_is_marker_and_pin_hole_reveals_map():
    fig, ax = canvas()
    try:
        markers.draw_markers(ax, [mark()], False, THEME, CROP, lambda x, y: (x, y), 1)
        fig.canvas.draw()
        assert np.array_equal(sample(fig, ax, 0, 0), [255, 0, 0])
    finally:
        plt.close(fig)
    fig, ax = canvas()
    try:
        ax.set_facecolor("#224488")
        fig.set_facecolor("#224488")
        markers.draw_markers(ax, [mark("pin")], False, THEME, CROP, lambda x, y: (x, y), 1)
        fig.canvas.draw()
        # The pin hole is at path y=62; size 72 maps path units to dpi/100 pixels.
        point = ax.transData.inverted().transform(ax.transData.transform((0, 0)) + [0, 62])
        assert np.array_equal(sample(fig, ax, *point), [34, 68, 136])
        assert np.array_equal(sample(fig, ax, 0, 8), [255, 0, 0])
    finally:
        plt.close(fig)


def test_size_and_label_offsets_in_points_at_each_dpi():
    extents = []
    for dpi in (100, 200):
        fig, ax = canvas(dpi)
        try:
            artists = markers.draw_markers(ax, [mark("pin", label="Our home")], False, THEME, CROP, lambda x, y: (x, y), 1)
            fig.canvas.draw()
            patch, label = artists[-2:]
            box = patch.get_window_extent()
            extents.append((box.width * 72 / dpi, box.height * 72 / dpi))
            assert label.get_text() == "Our home"
            assert label.get_va() == "baseline"
            assert label.get_fontsize() == 15
            baseline = label.get_transform().transform(label.get_position())[1]
            assert (box.ymin - baseline) * 72 / dpi == pytest.approx(4 + 0.75 * 15)
            assert label.get_zorder() == 101
        finally:
            plt.close(fig)
    assert extents[0] == pytest.approx(extents[1])
    assert extents[0] == pytest.approx((64 * 0.72, 94 * 0.72))


def test_outside_anchor_hides_marker_and_label_but_line_joins_all_positions():
    fig, ax = canvas()
    try:
        project = Mock(side_effect=lambda lon, lat: (lon, lat))
        artists = markers.draw_markers(ax, [mark(lat=0, lon=-150, label="Outside"), mark(label="Center"), mark(lat=10, lon=50)],
                                       True, THEME, CROP, project, 1)
        assert project.call_args_list[0].args == (-150, 0)
        assert [text.get_text() for text in ax.texts] == ["Center"]
        assert len(ax.patches) == 4
        line = artists[0]
        assert list(line.get_xdata()) == [-150, 0, 50]
        assert line.get_clip_on() and line.get_clip_box() is not None
        assert line.get_zorder() == 98
        assert line.get_dash_capstyle() == "round"
        assert line.is_dashed()
        assert line.get_linewidth() == 2.2
    finally:
        plt.close(fig)


@pytest.mark.parametrize("size,opacity,patches", [(72, 1, 2), (160, 0.6, 2), (161, 1, 1), (72, 0.59, 1)])
def test_halo_thresholds(size, opacity, patches):
    fig, ax = canvas()
    try:
        markers.draw_markers(ax, [mark(size=size, opacity=opacity)], False, THEME, CROP, lambda x, y: (x, y), 1)
        assert len(ax.patches) == patches
        if patches == 2:
            assert ax.patches[0].get_linewidth() == pytest.approx(size * 0.07)
            assert ax.patches[0].get_joinstyle() == "round"
    finally:
        plt.close(fig)


def test_legacy_heart_is_prepended_without_limiting_or_mutating_list():
    supplied = [mark("pin") for _ in range(5)]
    combined = markers.with_legacy_heart(supplied, True)
    assert len(combined) == 6
    assert len(supplied) == 5
    assert combined[0] == mark(color="#FF2A2A")


def test_run_generator_forwards_markers_and_legacy_flag(monkeypatch):
    create = Mock()
    monkeypatch.setattr(renderer, "create_poster", create)
    monkeypatch.setattr(renderer, "get_coordinates", lambda *args, **kwargs: (50, 30))
    supplied = [mark("pin")]
    renderer.run_generator("Kyiv", "Ukraine", markers=supplied, connect=True, show_heart=True)
    assert create.call_args.kwargs["markers"] == supplied
    assert create.call_args.kwargs["connect"] is True
    assert create.call_args.kwargs["show_heart"] is True


def test_renderer_uses_vector_legacy_heart_and_no_glyph(tmp_path, monkeypatch):
    import tiles
    monkeypatch.setattr(tiles, "load_layers", lambda *args, **kwargs: tiles.MapLayers())
    draw = Mock(wraps=markers.draw_markers)
    monkeypatch.setattr(renderer, "draw_markers", draw)
    output = tmp_path / "heart.svg"
    renderer.create_poster("Test", "Country", (0, 0), 1000, str(output), "svg", theme=renderer.load_theme("terracotta"),
                           width=2, height=2, show_heart=True)
    assert draw.call_args.args[1][0]["type"] == "heart"
    assert "♥" not in Path(renderer.__file__).read_text(encoding="utf-8")
    assert output.is_file()
