"""Vector poster marks, positioned in map coordinates and sized in points."""

from functools import lru_cache
import json
from pathlib import Path as FilePath
import re

from matplotlib import patheffects
from matplotlib.font_manager import FontProperties
from matplotlib.path import Path
from matplotlib.patches import PathPatch
from matplotlib.transforms import Affine2D, ScaledTranslation


SHAPES = json.loads(FilePath(__file__).with_name("marker_shapes.json").read_text(encoding="utf-8"))
_TOKEN = re.compile(r"[MLCZ]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")
LABEL_SIZE = 15
LINE_WIDTH = 2.2
_ROBOTO = FilePath(__file__).with_name("fonts") / "Roboto-Regular.ttf"


def parse_path(data, anchor=(0, 0)):
    """Parse absolute SVG M/L/C/Z commands, flipping y around the anchor."""
    tokens = _TOKEN.findall(data)
    if re.sub(r"[\s,]", "", _TOKEN.sub("", data)):
        raise ValueError("Unsupported SVG path command")
    vertices, codes = [], []
    cursor = 0
    command = None
    start = None
    while cursor < len(tokens):
        if tokens[cursor] in ("M", "L", "C", "Z"):
            command = tokens[cursor]
            cursor += 1
            if command != "Z" and cursor == len(tokens):
                raise ValueError("Incomplete path coordinates")
            if command == "Z":
                if start is None:
                    raise ValueError("A closed path needs a starting point")
                vertices.append(start)
                codes.append(Path.CLOSEPOLY)
                command = None
                continue
        if command is None:
            raise ValueError("Expected a path command")
        count = 6 if command == "C" else 2
        values = tokens[cursor:cursor + count]
        if len(values) != count or any(value in ("M", "L", "C", "Z") for value in values):
            raise ValueError("Incomplete path coordinates")
        values = [float(value) for value in values]
        cursor += count
        points = [(values[i] - anchor[0], anchor[1] - values[i + 1]) for i in range(0, count, 2)]
        if command == "M":
            start = points[0]
            codes.append(Path.MOVETO)
            command = "L"
        else:
            codes.extend([Path.CURVE4 if command == "C" else Path.LINETO] * len(points))
        vertices.extend(points)
    if not vertices:
        raise ValueError("Empty SVG path")
    return Path(vertices, codes, readonly=True)


@lru_cache(maxsize=None)
def shape_path(kind):
    """Return the shared immutable path for a mark shape."""
    shape = SHAPES[kind]
    return parse_path(shape["d"], tuple(shape["anchor"]))


def resolve_color(color, theme):
    return theme[color] if color in ("text", "road_motorway", "water") else color


def with_legacy_heart(markers, show_heart=False):
    """Prepend the legacy center heart without changing the supplied list."""
    result = list(markers or [])
    if show_heart:
        result.insert(0, dict(type="heart", lat=None, lon=None, size=72, opacity=1, color="#FF2A2A", label=""))
    return result


def draw_markers(ax, markers, connect, theme, crop, project, scale_factor):
    """Draw marks using a lon/lat-to-metres callable and (xlim, ylim) crop."""
    positions = [(0, 0) if marker.get("lat") is None else project(marker["lon"], marker["lat"]) for marker in markers]
    artists = []
    if connect and len(positions) >= 2:
        line, = ax.plot(*zip(*positions), color=theme["text"], alpha=0.75,
                        linewidth=LINE_WIDTH * scale_factor, linestyle=(0, (0.01, 2.6)),
                        dash_capstyle="round", zorder=98, clip_on=True)
        artists.append(line)
    for marker, (x, y) in zip(markers, positions):
        if not (crop[0][0] <= x <= crop[0][1] and crop[1][0] <= y <= crop[1][1]):
            continue
        path = shape_path(marker["type"])
        size = marker.get("size", 72)
        opacity = marker.get("opacity", 1)
        color = resolve_color(marker.get("color", "text"), theme)
        # Coordinates are in hundredths of the size in points, then inches,
        # then display pixels. Translation follows the live data transform.
        transform = (Affine2D().scale(size * scale_factor / 100 / 72)
                     + ax.figure.dpi_scale_trans + ScaledTranslation(x, y, ax.transData))
        if opacity >= 0.6 and size <= 160:
            halo = PathPatch(path, facecolor="none", edgecolor=theme["bg"],
                             linewidth=0.07 * size * scale_factor, joinstyle="round",
                             alpha=opacity, transform=transform, zorder=100, clip_on=True)
            ax.add_patch(halo)
            artists.append(halo)
        patch = PathPatch(path, facecolor=color, edgecolor="none", alpha=opacity,
                          transform=transform, zorder=100, clip_on=True)
        ax.add_patch(patch)
        artists.append(patch)
        label = marker.get("label", "")
        if label:
            bottom = -path.get_extents().ymin * size * scale_factor / 100
            baseline = -(bottom + (4 + 0.75 * LABEL_SIZE) * scale_factor)
            label_transform = (Affine2D().scale(1 / 72) + ax.figure.dpi_scale_trans
                               + ScaledTranslation(x, y, ax.transData))
            text = ax.text(0, baseline, label, transform=label_transform, ha="center", va="baseline", parse_math=False,
                           fontproperties=FontProperties(fname=str(_ROBOTO), size=LABEL_SIZE * scale_factor),
                           color=color, alpha=1, zorder=101, clip_on=True,
                           path_effects=[patheffects.withStroke(linewidth=3 * scale_factor, foreground=theme["bg"])])
            artists.append(text)
    return artists
