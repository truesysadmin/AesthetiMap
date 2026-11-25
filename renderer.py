#!/usr/bin/env python3
"""
City Map Poster Generator

This module generates beautiful, minimalist map posters for any city in the world.
It fetches OpenMapTiles vector data, applies customizable themes, and creates
high-quality poster-ready images with roads, water features, and parks.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import hashlib
import re
import tempfile
import traceback
import uuid
from functools import partial
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any

import matplotlib
import matplotlib.colors as mcolors
import numpy as np
from pyproj import Transformer
from geopy.geocoders import Nominatim
from lat_lon_parser import parse
from matplotlib.font_manager import FontProperties

from font_management import load_fonts
from markers import draw_markers, with_legacy_heart


import tiles
from tiles import GenerationCancelled, MapDataUnavailable, CityNotFound
from matplotlib.collections import LineCollection, PathCollection
from matplotlib.path import Path as PlotPath
from shapely.geometry.polygon import orient
from shapely.affinity import translate


matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

NoMapData = tiles.NoMapData

HTTP_USER_AGENT = os.environ.get("HTTP_USER_AGENT", "map-poster-service/1.0")


class CacheError(Exception):
    """Raised when a cache operation fails."""


CACHE_DIR_PATH = os.environ.get("CACHE_DIR", "cache")
CACHE_DIR = Path(CACHE_DIR_PATH)
CACHE_DIR.mkdir(exist_ok=True)

THEMES_DIR = "themes"
FONTS_DIR = "fonts"
POSTERS_DIR = "posters"

FILE_ENCODING = "utf-8"

FONTS = load_fonts()


def _cache_path(key: str) -> str:
    """
    Generate a safe cache file path from a cache key.

    Args:
        key: Cache key identifier

    Returns:
        Path to cache file with .json extension
    """
    safe = key.replace(os.sep, "_")
    return os.path.join(CACHE_DIR, f"{safe}.json")


def cache_get(key: str):
    """Return a cached object, deleting corrupt files as cache misses."""
    path = _cache_path(key)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except GenerationCancelled:
        raise
    except FileNotFoundError:
        return None
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None


def cache_set(key: str, value):
    """Atomically replace a cached object using a temporary sibling file."""
    temporary = None
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=CACHE_DIR, mode="w", encoding="utf-8", delete=False) as handle:
            temporary = handle.name
            json.dump(value, handle)
        os.replace(temporary, _cache_path(key))
    except GenerationCancelled:
        raise
    except Exception as exc:
        raise CacheError("Cache write failed") from exc
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


# Font loading now handled by font_management.py module


def is_latin_script(text):
    """
    Check if text is primarily Latin script.
    Used to determine if letter-spacing should be applied to city names.

    :param text: Text to analyze
    :return: True if text is primarily Latin script, False otherwise
    """
    if not text:
        return True

    latin_count = 0
    total_alpha = 0

    for char in text:
        if char.isalpha():
            total_alpha += 1
            # Latin Unicode ranges:
            # - Basic Latin: U+0000 to U+007F
            # - Latin-1 Supplement: U+0080 to U+00FF
            # - Latin Extended-A: U+0100 to U+017F
            # - Latin Extended-B: U+0180 to U+024F
            if ord(char) < 0x250:
                latin_count += 1

    # If no alphabetic characters, default to Latin (numbers, symbols, etc.)
    if total_alpha == 0:
        return True

    # Consider it Latin if >80% of alphabetic characters are Latin
    return (latin_count / total_alpha) > 0.8


def _validate_theme_name(theme_name):
    if not re.fullmatch(r"[a-z0-9_-]+", theme_name):
        raise ValueError("Invalid theme name")


def generate_output_filename(city, theme_name, output_format):
    """Generate a unique filename confined to the poster directory."""
    _validate_theme_name(theme_name)
    if output_format.lower() not in {"png", "svg", "pdf"}:
        raise ValueError("Invalid output format")
    directory = Path(POSTERS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", city.lower()).strip("_")[:40] or "poster"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{slug}_{theme_name}_{timestamp}_{uuid.uuid4().hex[:8]}.{output_format.lower()}"
    output = directory / filename
    if not output.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Invalid output path")
    return str(output)


def get_available_themes():
    """
    Scans the themes directory and returns a list of available theme names.
    """
    if not os.path.exists(THEMES_DIR):
        os.makedirs(THEMES_DIR)
        return []

    themes = []
    for file in sorted(os.listdir(THEMES_DIR)):
        if file.endswith(".json"):
            theme_name = file[:-5]  # Remove .json extension
            themes.append(theme_name)
    return themes


def load_theme(theme_name="terracotta"):
    """
    Load theme from JSON file in themes directory.
    """
    _validate_theme_name(theme_name)
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.json")
    if not os.path.isfile(theme_file):
        raise ValueError("Unknown theme")

    with open(theme_file, "r", encoding=FILE_ENCODING) as f:
        theme = json.load(f)
        print(f"✓ Loaded theme: {theme.get('name', theme_name)}")
        if "description" in theme:
            print(f"  {theme['description']}")
        return theme


# Load theme (can be changed via command line or input)
# theme is now passed as an argument to rendering functions for thread safety.


def _check_cancel(cancel_check=None):
    if cancel_check and cancel_check():
        raise GenerationCancelled()


def _cancellable_wait(seconds, cancel_check=None):
    _check_cancel(cancel_check)
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.1, remaining))
        _check_cancel(cancel_check)


def log_message(msg: str, callback: Optional[Callable[[str, Optional[int]], None]] = None,
                progress: Optional[int] = None, cancel_check=None):
    """Report progress while checking cancellation."""
    _check_cancel(cancel_check)
    print(msg)
    if callback:
        callback(msg, progress)


def format_coordinates(lat, lon):
    return f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'} / {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"


def create_gradient_fade(ax, color, location="bottom", zorder=10):
    """
    Creates a fade effect at the specified edge of the map.
    """
    if location in ("bottom", "top"):
        vals = np.linspace(0, 1, 256).reshape(-1, 1)
        gradient = np.hstack((vals, vals))
    else:
        vals = np.linspace(0, 1, 256).reshape(1, -1)
        gradient = np.vstack((vals, vals))

    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, :3] = rgb

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    x_range = xlim[1] - xlim[0]
    y_range = ylim[1] - ylim[0]

    if location == "bottom":
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent = [xlim[0], xlim[1], ylim[0], ylim[0] + y_range * 0.25]
    elif location == "top":
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent = [xlim[0], xlim[1], ylim[0] + y_range * 0.75, ylim[1]]
    elif location == "left":
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent = [xlim[0], xlim[0] + x_range * 0.25, ylim[0], ylim[1]]
    elif location == "right":
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent = [xlim[0] + x_range * 0.75, xlim[1], ylim[0], ylim[1]]

    custom_cmap = mcolors.ListedColormap(my_colors)

    ax.imshow(
        gradient,
        extent=extent,
        aspect="auto",
        cmap=custom_cmap,
        zorder=zorder,
        origin="lower",
    )


def get_coordinates(city, country, cancel_check=None):
    """Geocode a city with a private, normalized cache key."""
    _check_cancel(cancel_check)
    key = hashlib.sha256(f"{city}|{country}".lower().encode()).hexdigest()
    coords = f"coords_{key}"
    cached = cache_get(coords)
    if cached is not None:
        return tuple(cached)
    geolocator = Nominatim(user_agent=HTTP_USER_AGENT, timeout=10)
    _cancellable_wait(1, cancel_check)
    try:
        location = geolocator.geocode(f"{city}, {country}")
        if asyncio.iscoroutine(location):
            location = asyncio.run(location)
        _check_cancel(cancel_check)
    except GenerationCancelled:
        raise
    except Exception as exc:
        raise MapDataUnavailable("Geocoding service unavailable") from exc
    if location is None:
        raise CityNotFound("City not found")
    result = (location.latitude, location.longitude)
    try:
        cache_set(coords, result)
    except CacheError:
        pass
    return result


def crop_limits(width, height, span):
    aspect = width / height
    half_x = span / 2 * min(1, aspect)
    half_y = span / 2 / max(1, aspect)
    return (-half_x, half_x), (-half_y, half_y)


def draw_polygons(ax, polygons, color, zorder, alpha=1):
    """Keep individual polygons separate and orient interior rings as holes."""
    paths = []
    for polygon in polygons:
        polygon = orient(polygon, sign=1)
        rings = [np.asarray(ring.coords) for ring in [polygon.exterior, *polygon.interiors]]
        vertices = np.concatenate(rings)
        codes = np.concatenate([[PlotPath.MOVETO] + [PlotPath.LINETO] * (len(ring) - 2) + [PlotPath.CLOSEPOLY] for ring in rings])
        paths.append(PlotPath(vertices, codes))
    if paths:
        ax.add_collection(PathCollection(paths, facecolors=color, edgecolors="none", linewidths=0,
                                         alpha=alpha, zorder=zorder, transform=ax.transData))


def draw_lines(ax, lines, color, width, zorder):
    if lines:
        ax.add_collection(LineCollection([np.asarray(line.coords) for line in lines], colors=color,
                                         linewidths=width, capstyle="round", joinstyle="round", zorder=zorder))


def create_poster(
    city,
    country,
    point,
    span,
    output_file,
    output_format,
    theme: Dict[str, Any],
    width=12.0,
    height=16.0,
    country_label=None,
    name_label=None,
    display_city=None,
    display_country=None,
    fonts=None,
    no_title=False,
    no_coords=False,
    gradient_tb=False,
    gradient_lr=False,
    text_position="bottom",
    show_buildings=False,
    show_contours=False,
    show_heart=False,
    markers=None,
    connect=False,
    callback: Optional[Callable[[str, Optional[int]], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
):
    """
    Generate a complete map poster with roads, water, parks, and typography.

    Creates a high-quality poster by fetching OSM data, rendering map layers,
    applying the current theme, and adding text labels with coordinates.

    Args:
        city: City name for display on poster
        country: Country name for display on poster
        point: (latitude, longitude) tuple for map center
        span: Map coverage (span) in meters
        output_file: Path where poster will be saved
        output_format: File format ('png', 'svg', or 'pdf')
        width: Poster width in inches (default: 12)
        height: Poster height in inches (default: 16)
        country_label: Optional override for country text on poster
        _name_label: Optional override for city name (unused, reserved for future use)
        callback: Optional callback(message, progress_percent) for status updates

    Raises:
        RuntimeError: If street network data cannot be retrieved
    """
    log = partial(log_message, cancel_check=cancel_check)
    _check_cancel(cancel_check)
    # Handle display names for i18n support
    # Priority: display_city/display_country > name_label/country_label > city/country
    display_city = display_city or name_label or city
    display_country = display_country or country_label or country

    log(f"--- Generating Map for {city}, {country} ---", callback, 2)

    log("Calculating optimal bounding box...", callback, 5)
    crop_xlim, crop_ylim = crop_limits(width, height, span)
    half_x, half_y = crop_xlim[1], crop_ylim[1]
    bbox_tuple = tiles.bounding_box(point, half_x, half_y)
    layers = tiles.load_layers(point, half_x, half_y, callback, cancel_check, show_buildings=show_buildings)
    scale_factor = min(width, height) / 12

    log("Initializing map layers...", callback, 60)
    fig, ax = plt.subplots(figsize=(width, height), facecolor=theme["bg"])
    try:
        ax.set_facecolor(theme["bg"])
        ax.set_position((0.0, 0.0, 1.0, 1.0))

        draw_polygons(ax, layers.water, theme['water'], 0.5)
        draw_polygons(ax, layers.parks, theme['parks'], 0.8)
        for kind, line_width in {"river": 1.0, "canal": 0.6, "stream": 0.3}.items():
            draw_lines(ax, layers.waterways[kind], theme['water'], line_width * scale_factor, 0.6)

        # Layer 1.5: Elevation Contours (Optional)
        # Render here to be under roads and buildings
        if show_contours:
            log("Downloading SRTM elevation data...", callback, 78)
            try:
                import elevation
                import rasterio
                from rasterio.transform import rowcol

                west, south, east, north = bbox_tuple
                # Add small buffer to avoid edge interpolation artifacts
                buf = 0.01
                bounds = (west - buf, south - buf, east + buf, north + buf)

                # Use a slugified bounding box as cache key for the GeoTIFF
                bbox_slug = f"{south:.3f}_{west:.3f}_{north:.3f}_{east:.3f}".replace("-", "m").replace(".", "d")
                dem_path = os.path.join(CACHE_DIR, f"srtm_{bbox_slug}.tif")

                if not os.path.exists(dem_path):
                    log("  Fetching SRTM30 tiles from NASA/USGS...", callback, 80)
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=CACHE_DIR, suffix=".tif", delete=False) as handle:
                            temporary = handle.name
                        elevation.clip(bounds=bounds, output=os.path.abspath(temporary), product="SRTM1")
                        _check_cancel(cancel_check)
                        os.replace(temporary, dem_path)
                        elevation.clean()
                    finally:
                        if temporary and os.path.exists(temporary):
                            os.unlink(temporary)
                else:
                    log("  ✓ Using cached SRTM elevation data", callback, 82)

                log("Processing topography contours...", callback, 85)

                with rasterio.open(dem_path) as src:
                    grid_size = 60
                    x_grid, y_grid = np.meshgrid(np.linspace(-half_x * 1.02, half_x * 1.02, grid_size),
                                                 np.linspace(-half_y * 1.02, half_y * 1.02, grid_size))
                    inverse = Transformer.from_crs(tiles.local_crs(point), "EPSG:4326", always_xy=True)
                    flat_lons, flat_lats = inverse.transform(x_grid.ravel(), y_grid.ravel())

                    # Read elevation values via row/col lookup (fast, vectorized)
                    rows, cols = rowcol(src.transform, flat_lons, flat_lats)
                    rows = np.clip(rows, 0, src.height - 1)
                    cols = np.clip(cols, 0, src.width - 1)
                    data = src.read(1)
                    all_elevations = data[rows, cols].astype(float)

                    # Replace SRTM nodata values with NaN then interpolate
                    nodata = src.nodata or -32768
                    all_elevations[all_elevations == nodata] = np.nan
                    if np.isnan(all_elevations).any():
                        nans = np.isnan(all_elevations)
                        valid = ~nans
                        if valid.any():
                            all_elevations[nans] = np.interp(
                                np.flatnonzero(nans), np.flatnonzero(valid), all_elevations[valid]
                            )

                elevations = all_elevations.reshape(x_grid.shape)

                min_elev, max_elev = np.nanmin(elevations), np.nanmax(elevations)
                log(f"  Elevation range: {min_elev:.0f}m – {max_elev:.0f}m", callback)
                if max_elev - min_elev > 5:
                    ax.contour(
                        x_grid, y_grid, elevations, levels=25,
                        colors=theme['text'], alpha=0.18, linewidths=0.25, zorder=0.7
                    )
                else:
                    log("ℹ Elevation range too small for contours", callback)
            except GenerationCancelled:
                raise
            except Exception:
                traceback.print_exc()
                log("⚠ Elevation rendering failed", callback)

        log("Applying road hierarchy colors...", callback, 86)
        widths = {"default": 0.4, "residential": 0.4, "tertiary": 0.6, "secondary": 0.8, "primary": 1.0, "motorway": 1.2}
        for kind, line_width in widths.items():
            color = theme.get(f"road_{kind}", theme['road_tertiary'])
            draw_lines(ax, layers.roads[kind], color, line_width * scale_factor, 1)
        if show_buildings:
            log("Rendering building footprints...", callback, 88)
            shadow_offset = 0.02 * (span / width)
            shadows = [translate(polygon, xoff=shadow_offset, yoff=-shadow_offset) for polygon in layers.building_shadows]
            draw_polygons(ax, shadows, theme.get('text', '#000000'), 2, alpha=0.08)
            draw_polygons(ax, layers.buildings, theme.get('building', theme['road_residential']), 3)

        log("Finalizing map rendering...", callback, 92)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(crop_xlim)
        ax.set_ylim(crop_ylim)
        ax.axis("off")

        # Layer 3: Gradients
        if gradient_tb:
            create_gradient_fade(ax, theme['gradient_color'], location='bottom', zorder=10)
            create_gradient_fade(ax, theme['gradient_color'], location='top', zorder=10)
        if gradient_lr:
            create_gradient_fade(ax, theme['gradient_color'], location='left', zorder=10)
            create_gradient_fade(ax, theme['gradient_color'], location='right', zorder=10)

        # Typography
        scale_factor = min(height, width) / 12.0

        poster_markers = with_legacy_heart(markers, show_heart)
        if poster_markers:
            log("Rendering marks...", callback, 93)
            projection = Transformer.from_crs("EPSG:4326", tiles.local_crs(point), always_xy=True)
            draw_markers(ax, poster_markers, connect, theme, (crop_xlim, crop_ylim), projection.transform, scale_factor)

        base_main = 60
        base_sub = 22
        base_coords = 14
        base_attr = 8

        active_fonts = fonts or FONTS
        if active_fonts:
            font_sub = FontProperties(fname=active_fonts["light"], size=base_sub * scale_factor)
            font_coords = FontProperties(fname=active_fonts["regular"], size=base_coords * scale_factor)
            font_attr = FontProperties(fname=active_fonts["light"], size=base_attr * scale_factor)
        else:
            font_sub = FontProperties(family="monospace", weight="normal", size=base_sub * scale_factor)
            font_coords = FontProperties(family="monospace", size=base_coords * scale_factor)
            font_attr = FontProperties(family="monospace", size=base_attr * scale_factor)

        if is_latin_script(display_city):
            spaced_city = "  ".join(list(display_city.upper()))
        else:
            spaced_city = display_city

        base_adjusted_main = base_main * scale_factor
        city_char_count = len(display_city)
        if city_char_count > 10:
            length_factor = 10 / city_char_count
            adjusted_font_size = max(base_adjusted_main * length_factor, 10 * scale_factor)
        else:
            adjusted_font_size = base_adjusted_main

        if active_fonts:
            font_main_adjusted = FontProperties(fname=active_fonts["bold"], size=adjusted_font_size)
        else:
            font_main_adjusted = FontProperties(family="monospace", weight="bold", size=adjusted_font_size)

        if text_position == "top":
            y_city, y_sep, y_country, y_coords = 0.88, 0.862, 0.83, 0.80
        elif text_position == "center":
            y_city, y_sep, y_country, y_coords = 0.56, 0.542, 0.51, 0.48
        else:  # bottom
            y_city, y_sep, y_country, y_coords = 0.14, 0.125, 0.10, 0.07

        if not no_title:
            ax.text(0.5, y_city, spaced_city, transform=ax.transAxes, color=theme["text"],
                    ha="center", fontproperties=font_main_adjusted, zorder=11, parse_math=False)
            ax.text(0.5, y_country, display_country.upper(), transform=ax.transAxes, color=theme["text"],
                    ha="center", fontproperties=font_sub, zorder=11, parse_math=False)
            ax.plot([0.4, 0.6], [y_sep, y_sep], transform=ax.transAxes, color=theme["text"], linewidth=1 * scale_factor, zorder=11)

        if not no_coords:
            lat, lon = point
            coords_text = format_coordinates(lat, lon)
            ax.text(0.5, y_coords, coords_text, transform=ax.transAxes, color=theme["text"], alpha=0.7, ha="center", fontproperties=font_coords, zorder=11)

        ax.text(
            0.98, 0.02, "© OpenMapTiles © OpenStreetMap contributors · aesthetimap.rastiegaiev.com",
            transform=ax.transAxes, color=theme["text"], alpha=0.7, ha="right", va="bottom",
            fontproperties=font_attr, zorder=11,
        )

        # 5. Save
        log("Saving poster...", callback, 95)
        fmt = output_format.lower()
        save_kwargs = dict(facecolor=theme["bg"], bbox_inches="tight", pad_inches=0.05)
        if fmt == "png":
            save_kwargs["dpi"] = 300
        fig.savefig(output_file, format=fmt, **save_kwargs)
        log("✓ Poster saved", callback, 100)
    except BaseException:
        try:
            os.unlink(output_file)
        except OSError:
            pass
        raise
    finally:
        plt.close(fig)


def run_generator(
    city: str,
    country: str,
    theme: str = "terracotta",
    span: int = 20000,
    width: float = 12.0,
    height: float = 16.0,
    output_format: str = "png",
    latitude: Optional[str] = None,
    longitude: Optional[str] = None,
    no_title: bool = False,
    no_coords: bool = False,
    gradient_tb: bool = False,
    gradient_lr: bool = False,
    text_position: str = "bottom",
    country_label: Optional[str] = None,
    display_city: Optional[str] = None,
    display_country: Optional[str] = None,
    font_family: Optional[str] = None,
    show_buildings: bool = False,
    show_contours: bool = False,
    show_heart: bool = False,
    markers=None,
    connect: bool = False,
    callback: Optional[Callable[[str, Optional[int]], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
):
    """Entry point for library calls."""
    _check_cancel(cancel_check)
    custom_fonts = load_fonts(font_family) if font_family else None
    if latitude and longitude:
        coords = (parse(latitude), parse(longitude))
    else:
        coords = get_coordinates(city, country, cancel_check=cancel_check)

    loaded_theme = load_theme(theme)
    output_file = generate_output_filename(city, theme, output_format)
    create_poster(
        city, country, coords, span, output_file, output_format,
        theme=loaded_theme,
        width=width, height=height, country_label=country_label,
        display_city=display_city, display_country=display_country,
        fonts=custom_fonts, no_title=no_title, no_coords=no_coords,
        gradient_tb=gradient_tb, gradient_lr=gradient_lr,
        text_position=text_position, show_buildings=show_buildings,
        show_contours=show_contours, show_heart=show_heart, markers=markers, connect=connect, callback=callback, cancel_check=cancel_check
    )
    return output_file


def print_examples():
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  python renderer.py --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  python renderer.py -c "New York" -C "USA" -t noir --span 24000           # Manhattan grid
  python renderer.py -c "Barcelona" -C "Spain" -t warm_beige --span 16000 # Eixample district grid

  # Waterfront & canals
  python renderer.py -c "Venice" -C "Italy" -t blueprint --span 4000       # Canal network
  python renderer.py -c "Amsterdam" -C "Netherlands" -t ocean --span 6000  # Concentric canals
  python renderer.py -c "Dubai" -C "UAE" -t midnight_blue --span 15000     # Palm & coastline

  # Radial patterns
  python renderer.py -c "Paris" -C "France" -t pastel_dream --span 10000   # Haussmann boulevards

  # Organic old cities
  python renderer.py -c "Tokyo" -C "Japan" -t japanese_ink --span 15000    # Dense organic streets
  python renderer.py -c "Marrakech" -C "Morocco" -t terracotta --span 5000 # Medina maze
  python renderer.py -c "Rome" -C "Italy" -t warm_beige --span 8000        # Ancient street layout

  # Coastal cities
  python renderer.py -c "San Francisco" -C "USA" -t sunset --span 10000    # Peninsula grid
  python renderer.py -c "Sydney" -C "Australia" -t ocean --span 12000      # Harbor city
  python renderer.py -c "Mumbai" -C "India" -t contrast_zones --span 18000 # Coastal peninsula

  # River cities
  python renderer.py -c "London" -C "UK" -t noir --span 15000              # Thames curves
  python renderer.py -c "Budapest" -C "Hungary" -t copper_patina --span 8000  # Danube split

  # List themes
  python renderer.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --country-label   Override country text displayed on poster
  --theme, -t       Theme name (default: terracotta)
  --all-themes      Generate posters for all themes
  --span, -d        Map span in meters (default: 20000)
  --list-themes     List all available themes

Span guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes can be found in the 'themes/' directory.
Generated posters are saved to 'posters/' directory.
""")


def list_themes():
    """List all available themes with descriptions."""
    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        return

    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
        try:
            with open(theme_path, "r", encoding=FILE_ENCODING) as f:
                theme_data = json.load(f)
                display_name = theme_data.get('name', theme_name)
                description = theme_data.get('description', '')
        except (OSError, json.JSONDecodeError):
            display_name = theme_name
            description = ""
        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python renderer.py --city "New York" --country "USA"
  python renderer.py --city "New York" --country "USA" --latitude 40.776676 --longitude -73.971321 --theme neon_cyberpunk
  python renderer.py --city Tokyo --country Japan --theme midnight_blue
  python renderer.py --city Paris --country France --theme noir --span 15000
  python renderer.py --list-themes
        """,
    )

    parser.add_argument("--city", "-c", type=str, help="City name")
    parser.add_argument("--country", "-C", type=str, help="Country name")
    parser.add_argument(
        "--latitude",
        "-lat",
        dest="latitude",
        type=str,
        help="Override latitude center point",
    )
    parser.add_argument(
        "--longitude",
        "-long",
        dest="longitude",
        type=str,
        help="Override longitude center point",
    )
    parser.add_argument(
        "--country-label",
        dest="country_label",
        type=str,
        help="Override country text displayed on poster",
    )
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        default="terracotta",
        help="Theme name (default: terracotta)",
    )
    parser.add_argument(
        "--all-themes",
        "--All-themes",
        dest="all_themes",
        action="store_true",
        help="Generate posters for all themes",
    )
    parser.add_argument(
        "--span",
        "-d",
        type=int,
        default=20000,
        help="Map coverage (span) in meters (default: 20000)",
    )
    parser.add_argument(
        "--width",
        "-W",
        type=float,
        default=12,
        help="Image width in inches (default: 12, max: 40)",
    )
    parser.add_argument(
        "--height",
        "-H",
        type=float,
        default=16,
        help="Image height in inches (default: 16, max: 40)",
    )
    parser.add_argument(
        "--list-themes", action="store_true", help="List all available themes"
    )
    parser.add_argument(
        "--display-city",
        "-dc",
        type=str,
        help="Custom display name for city (for i18n support)",
    )
    parser.add_argument(
        "--display-country",
        "-dC",
        type=str,
        help="Custom display name for country (for i18n support)",
    )
    parser.add_argument(
        "--font-family",
        type=str,
        help='Google Fonts family name (e.g., "Noto Sans JP", "Open Sans"). If not specified, uses local Roboto fonts.',
    )
    parser.add_argument(
        "--format",
        "-f",
        default="png",
        choices=["png", "svg", "pdf"],
        help="Output format for the poster (default: png)",
    )
    parser.add_argument(
        "--no-title",
        action="store_true",
        help="Do not draw city/country title and dividing line",
    )
    parser.add_argument(
        "--no-coords",
        action="store_true",
        help="Do not draw coordinates text",
    )
    parser.add_argument(
        "--gradient-tb",
        action="store_true",
        help="Draw top and bottom fade gradients",
    )
    parser.add_argument(
        "--gradient-lr",
        action="store_true",
        help="Draw left and right fade gradients",
    )
    parser.add_argument(
        "--text-position",
        dest="text_position",
        type=str,
        default="bottom",
        choices=["bottom", "top", "center"],
        help="Position of the city/country text block (default: bottom)",
    )
    parser.add_argument(
        "--show-buildings",
        action="store_true",
        help="Show building footprints with shadows",
    )
    parser.add_argument(
        "--show-contours",
        action="store_true",
        help="Show topography contours",
    )

    parser.add_argument("--show-heart", action="store_true", help="Show a heart at the map center")
    args = parser.parse_args()

    # If no arguments provided, show examples
    if len(sys.argv) == 1:
        print_examples()
        sys.exit(0)

    # List themes if requested
    if args.list_themes:
        list_themes()
        sys.exit(0)

    # Validate required arguments
    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        sys.exit(1)

    # Enforce maximum dimensions
    if args.width > 40:
        print(
            f"⚠ Width {args.width} exceeds the maximum allowed limit of 40. It's enforced as max limit 40."
        )
        args.width = 40.0
    if args.height > 40:
        print(
            f"⚠ Height {args.height} exceeds the maximum allowed limit of 40. It's enforced as max limit 40."
        )
        args.height = 40.0

    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        sys.exit(1)

    if args.all_themes:
        themes_to_generate = available_themes
    else:
        if args.theme not in available_themes:
            print(f"Error: Theme '{args.theme}' not found.")
            print(f"Available themes: {', '.join(available_themes)}")
            sys.exit(1)
        themes_to_generate = [args.theme]

    print("=" * 50)
    print("City Map Poster Generator")
    print("=" * 50)

    # Load custom fonts if specified
    custom_fonts = None
    if args.font_family:
        custom_fonts = load_fonts(args.font_family)
        if not custom_fonts:
            print(f"⚠ Failed to load '{args.font_family}', falling back to Roboto")

    # Get coordinates and generate poster
    try:
        if args.latitude and args.longitude:
            lat = parse(args.latitude)
            lon = parse(args.longitude)
            coords = [lat, lon]
            print(f"✓ Coordinates: {', '.join([str(i) for i in coords])}")
        else:
            coords = get_coordinates(args.city, args.country)

        for theme_name in themes_to_generate:
            THEME = load_theme(theme_name)
            output_file = generate_output_filename(args.city, theme_name, args.format)
            create_poster(
                args.city,
                args.country,
                coords,
                args.span,
                output_file,
                args.format,
                theme=THEME,
                width=args.width,
                height=args.height,
                country_label=args.country_label,
                display_city=args.display_city,
                display_country=args.display_country,
                fonts=custom_fonts,
                no_title=args.no_title,
                no_coords=args.no_coords,
                gradient_tb=args.gradient_tb,
                gradient_lr=args.gradient_lr,
                text_position=args.text_position,
                show_buildings=args.show_buildings,
                show_contours=args.show_contours,
                show_heart=args.show_heart,
            )

        print("\n" + "=" * 50)
        print("✓ Poster generation complete!")
        print("=" * 50)

    except GenerationCancelled:
        raise
    except Exception as e:
        print(f"\n✗ Error: {e}")

        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
