#!/usr/bin/env python3
"""
City Map Poster Generator

This module generates beautiful, minimalist map posters for any city in the world.
It fetches OpenStreetMap data using OSMnx, applies customizable themes, and creates
high-quality poster-ready images with roads, water features, and parks.
"""

import argparse
import asyncio
import json
import os
import pickle
import glob
import requests
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, cast, Callable, Dict, Any

import matplotlib
matplotlib.use('Agg')
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import pandas as pd
from pyproj import Transformer
from geopandas import GeoDataFrame
from geopy.distance import distance
from geopy.geocoders import Nominatim
from lat_lon_parser import parse
from matplotlib.font_manager import FontProperties
from networkx import MultiDiGraph
from shapely.geometry import Point

from font_management import load_fonts


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
        Path to cache file with .pkl extension
    """
    safe = key.replace(os.sep, "_")
    return os.path.join(CACHE_DIR, f"{safe}.pkl")


def cache_get(key: str):
    """
    Retrieve a cached object by key.

    Args:
        key: Cache key identifier

    Returns:
        Cached object if found, None otherwise

    Raises:
        CacheError: If cache read operation fails
    """
    try:
        path = _cache_path(key)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        raise CacheError(f"Cache read failed: {e}") from e


def cache_set(key: str, value):
    """
    Store an object in the cache.

    Args:
        key: Cache key identifier
        value: Object to cache (must be picklable)

    Raises:
        CacheError: If cache write operation fails
    """
    try:
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        path = _cache_path(key)
        with open(path, "wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        raise CacheError(f"Cache write failed: {e}") from e


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


def generate_output_filename(city, theme_name, output_format):
    """
    Generate unique output filename with city, theme, and datetime.
    """
    if not os.path.exists(POSTERS_DIR):
        os.makedirs(POSTERS_DIR)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(" ", "_")
    ext = output_format.lower()
    filename = f"{city_slug}_{theme_name}_{timestamp}.{ext}"
    return os.path.join(POSTERS_DIR, filename)


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
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.json")

    if not os.path.exists(theme_file):
        print(f"⚠ Theme file '{theme_file}' not found. Using default terracotta theme.")
        # Fallback to embedded terracotta theme
        return {
            "name": "Terracotta",
            "description": "Mediterranean warmth - burnt orange and clay tones on cream",
            "bg": "#F5EDE4",
            "text": "#8B4513",
            "gradient_color": "#F5EDE4",
            "water": "#A8C4C4",
            "parks": "#E8E0D0",
            "road_motorway": "#A0522D",
            "road_primary": "#B8653A",
            "road_secondary": "#C9846A",
            "road_tertiary": "#D9A08A",
            "road_residential": "#E5C4B0",
            "road_default": "#D9A08A",
        }

    with open(theme_file, "r", encoding=FILE_ENCODING) as f:
        theme = json.load(f)
        print(f"✓ Loaded theme: {theme.get('name', theme_name)}")
        if "description" in theme:
            print(f"  {theme['description']}")
        return theme


# Load theme (can be changed via command line or input)
# theme is now passed as an argument to rendering functions for thread safety.


def log_message(msg: str, callback: Optional[Callable[[str, Optional[int]], None]] = None, progress: Optional[int] = None):
    """Helper to log message and trigger callback if provided."""
    print(msg)
    if callback:
        callback(msg, progress)


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


def get_edge_colors_by_type(g, theme: Dict[str, Any]):
    """
    Assigns colors to edges based on road type hierarchy.
    Returns a list of colors corresponding to each edge in the graph.
    """
    edge_colors = []

    for _u, _v, data in g.edges(data=True):
        # Get the highway type (can be a list or string)
        highway = data.get('highway', 'unclassified')

        # Handle list of highway types (take the first one)
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'

        # Assign color based on road type
        if highway in ["motorway", "motorway_link"]:
            color = theme["road_motorway"]
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            color = theme["road_primary"]
        elif highway in ["secondary", "secondary_link"]:
            color = theme["road_secondary"]
        elif highway in ["tertiary", "tertiary_link"]:
            color = theme["road_tertiary"]
        elif highway in ["residential", "living_street", "unclassified"]:
            color = theme["road_residential"]
        else:
            color = theme.get('road_default', theme.get('road_tertiary', '#888888'))

        edge_colors.append(color)

    return edge_colors


def get_edge_widths_by_type(g):
    """
    Assigns line widths to edges based on road type.
    Major roads get thicker lines.
    """
    edge_widths = []

    for _u, _v, data in g.edges(data=True):
        highway = data.get('highway', 'unclassified')

        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'

        # Assign width based on road importance
        if highway in ["motorway", "motorway_link"]:
            width = 1.2
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            width = 1.0
        elif highway in ["secondary", "secondary_link"]:
            width = 0.8
        elif highway in ["tertiary", "tertiary_link"]:
            width = 0.6
        else:
            width = 0.4

        edge_widths.append(width)

    return edge_widths


def get_coordinates(city, country):
    """
    Fetches coordinates for a given city and country using geopy.
    Includes rate limiting to be respectful to the geocoding service.
    """
    coords = f"coords_{city.lower()}_{country.lower()}"
    cached = cache_get(coords)
    if cached:
        print(f"✓ Using cached coordinates for {city}, {country}")
        return cached

    print("Looking up coordinates...")
    geolocator = Nominatim(user_agent="city_map_poster", timeout=10)

    # Add a small delay to respect Nominatim's usage policy
    time.sleep(1)

    try:
        location = geolocator.geocode(f"{city}, {country}")
    except Exception as e:
        raise ValueError(f"Geocoding failed for {city}, {country}: {e}") from e

    # If geocode returned a coroutine in some environments, run it to get the result.
    if asyncio.iscoroutine(location):
        try:
            location = asyncio.run(location)
        except RuntimeError as exc:
            # If an event loop is already running, try using it to complete the coroutine.
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running event loop in the same thread; raise a clear error.
                raise RuntimeError(
                    "Geocoder returned a coroutine while an event loop is already running. "
                    "Run this script in a synchronous environment."
                ) from exc
            location = loop.run_until_complete(location)

    if location:
        # Use getattr to safely access address (helps static analyzers)
        addr = getattr(location, "address", None)
        if addr:
            print(f"✓ Found: {addr}")
        else:
            print("✓ Found location (address not available)")
        print(f"✓ Coordinates: {location.latitude}, {location.longitude}")
        try:
            cache_set(coords, (location.latitude, location.longitude))
        except CacheError as e:
            print(e)
        return (location.latitude, location.longitude)

    raise ValueError(f"Could not find coordinates for {city}, {country}")


def get_crop_limits(g_proj, center_lat_lon, fig, span):
    """
    Crop inward to preserve aspect ratio while guaranteeing
    full coverage of the requested span (total width/height).
    """
    lat, lon = center_lat_lon
    dist = span / 2

    # Project center point into graph CRS
    center = ox.projection.project_geometry(
        Point(lon, lat),
        crs="EPSG:4326",
        to_crs=g_proj.graph["crs"]
    )
    if isinstance(center, tuple):
        center = center[0]
    center_x, center_y = center.x, center.y

    fig_width, fig_height = fig.get_size_inches()
    aspect = fig_width / fig_height

    # Start from the *requested* radius
    half_x = dist
    half_y = dist

    # Cut inward to match aspect
    if aspect > 1:  # landscape → reduce height
        half_y = half_x / aspect
    else:  # portrait → reduce width
        half_x = half_y * aspect

    return (
        (center_x - half_x, center_x + half_x),
        (center_y - half_y, center_y + half_y),
    )


def fetch_graph(bbox) -> MultiDiGraph:
    """
    Fetch street network graph from OpenStreetMap inside a bounding box.
    """
    w, s, e, n = bbox
    graph_name = f"graph_{w:.4f}_{s:.4f}_{e:.4f}_{n:.4f}"
    cached = cache_get(graph_name)
    if cached is not None:
        print("✓ Using cached street network")
        return cast(MultiDiGraph, cached)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            g = ox.graph_from_bbox(bbox=bbox, network_type='all', truncate_by_edge=True)
            # Rate limit between requests
            time.sleep(0.5)
            try:
                cache_set(graph_name, g)
            except CacheError as err:
                print(err)
            return g
        except Exception as err:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                print(f"⚠ OSMnx error while fetching graph (Attempt {attempt+1}/{max_retries}): {err}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"✖ Failed to fetch graph after {max_retries} attempts: {err}")
                raise RuntimeError(f"Failed to retrieve street network data: {err}") from err


def fetch_features(bbox, tags, name) -> GeoDataFrame:
    """
    Fetch geographic features (water, parks, etc.) from OpenStreetMap in bbox.
    """
    w, s, e, n = bbox
    tag_str = "_".join(tags.keys())
    features_name = f"{name}_{w:.4f}_{s:.4f}_{e:.4f}_{n:.4f}_{tag_str}"
    cached = cache_get(features_name)
    if cached is not None:
        print(f"✓ Using cached {name}")
        return cast(GeoDataFrame, cached)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            data = ox.features_from_bbox(bbox=bbox, tags=tags)
            # Rate limit between requests
            time.sleep(0.3)
            try:
                cache_set(features_name, data)
            except CacheError as err:
                print(err)
            return data
        except Exception as err:
            # Check if it is a "no results" error which is not a failure we should retry
            if "No features found" in str(err) or "found no features" in str(err).lower():
                print(f"ℹ No features found for {name} in this area.")
                return GeoDataFrame() # Return empty GDF
                
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                print(f"⚠ OSMnx error while fetching features {name} (Attempt {attempt+1}/{max_retries}): {err}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"✖ Failed to fetch features {name} after {max_retries} attempts: {err}")
                # For features, we might want to return an empty GDF instead of failing entirely, 
                # but let's raise an error to let the caller decide if it should be retried at a higher level
                raise RuntimeError(f"Failed to retrieve {name} features: {err}") from err


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
    callback: Optional[Callable[[str, Optional[int]], None]] = None,
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
    # Handle display names for i18n support
    # Priority: display_city/display_country > name_label/country_label > city/country
    display_city = display_city or name_label or city
    display_country = display_country or country_label or country

    log_message(f"--- Generating Map for {city}, {country} ---", callback, 2)

    log_message("Calculating optimal bounding box...", callback, 5)
    aspect = width / height
    buffer_margin = 1.15
    lat, lon = point
    dist = span / 2
    if aspect < 1.0: # Portrait
        dist_ns = dist * buffer_margin
        dist_ew = dist * aspect * buffer_margin
    else: # Landscape
        dist_ew = dist * buffer_margin
        dist_ns = (dist / aspect) * buffer_margin
        
    north = distance(meters=dist_ns).destination(point, 0).latitude
    south = distance(meters=dist_ns).destination(point, 180).latitude
    east = distance(meters=dist_ew).destination(point, 90).longitude
    west = distance(meters=dist_ew).destination(point, 270).longitude
    bbox_tuple = (west, south, east, north)

    # 1. Fetch Street Network
    log_message("Downloading street network from OSM...", callback, 10)
    g = fetch_graph(bbox_tuple)
    if g is None:
        raise RuntimeError("Failed to retrieve street network data.")

    # 2. Fetch Water Features
    log_message("Downloading water features...", callback, 25)
    water = fetch_features(
        bbox_tuple,
        tags={"natural": ["water", "bay", "strait"], "waterway": "riverbank"},
        name="water",
    )

    # 3. Fetch Parks
    log_message("Downloading green areas...", callback, 35)
    parks = fetch_features(
        bbox_tuple,
        tags={"leisure": "park", "landuse": "grass", "natural": "wood", "landuse": "forest"},
        name="parks",
    )
    
    # 4. Fetch Buildings (Optional)
    buildings = None
    if show_buildings:
        log_message("Downloading building footprints (this may take a while)...", callback, 45)
        buildings = fetch_features(
            bbox_tuple,
            tags={"building": True},
            name="buildings",
        )

    log_message("Initializing map layers...", callback, 65)
    fig, ax = plt.subplots(figsize=(width, height), facecolor=theme["bg"])
    ax.set_facecolor(theme["bg"])
    ax.set_position((0.0, 0.0, 1.0, 1.0))

    # Project graph to a metric CRS so distances and aspect are linear (meters)
    log_message("Projecting street network...", callback, 68)
    g_proj = ox.project_graph(g)

    # Layer 1: Water and Green Spaces
    if water is not None and not water.empty:
        log_message("Rendering water features...", callback, 72)
        water_polys = water[water.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not water_polys.empty:
            try:
                water_polys = ox.projection.project_gdf(water_polys)
            except Exception:
                water_polys = water_polys.to_crs(g_proj.graph['crs'])
            water_polys.plot(ax=ax, facecolor=theme['water'], edgecolor='none', zorder=0.5)
            
    if parks is not None and not parks.empty:
        log_message("Rendering green areas...", callback, 75)
        parks_polys = parks[parks.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not parks_polys.empty:
            try:
                parks_polys = ox.projection.project_gdf(parks_polys)
            except Exception:
                parks_polys = parks_polys.to_crs(g_proj.graph['crs'])
            parks_polys.plot(ax=ax, facecolor=theme['parks'], edgecolor='none', zorder=0.8)
            
    # Layer 1.5: Elevation Contours (Optional)
    # Render here to be under roads and buildings
    if show_contours:
        log_message("Downloading SRTM elevation data...", callback, 78)
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
            dem_path = os.path.join(CACHE_DIR_PATH, f"srtm_{bbox_slug}.tif")

            if not os.path.exists(dem_path):
                log_message("  Fetching SRTM30 tiles from NASA/USGS...", callback, 80)
                elevation.clip(bounds=bounds, output=os.path.abspath(dem_path), product="SRTM1")
                elevation.clean()
            else:
                log_message("  ✓ Using cached SRTM elevation data", callback, 82)

            log_message("Processing topography contours...", callback, 85)

            with rasterio.open(dem_path) as src:
                # Sample a regular grid projected in the graph CRS
                grid_size = 60
                lats = np.linspace(south, north, grid_size)
                lons = np.linspace(west, east, grid_size)
                lon_grid, lat_grid = np.meshgrid(lons, lats)
                flat_lons = lon_grid.flatten()
                flat_lats = lat_grid.flatten()

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

            elevations = all_elevations.reshape(lat_grid.shape)

            # Project grid to graph CRS for plotting
            target_crs = g_proj.graph["crs"]
            transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
            x_flat, y_flat = transformer.transform(flat_lons, flat_lats)
            x_grid = x_flat.reshape(lon_grid.shape)
            y_grid = y_flat.reshape(lat_grid.shape)

            min_elev, max_elev = np.nanmin(elevations), np.nanmax(elevations)
            log_message(f"  Elevation range: {min_elev:.0f}m – {max_elev:.0f}m", callback)
            if max_elev - min_elev > 5:
                ax.contour(
                    x_grid, y_grid, elevations, levels=25,
                    colors=theme['text'], alpha=0.18, linewidths=0.25, zorder=0.7
                )
            else:
                log_message("ℹ Elevation range too small for contours", callback)
        except Exception as e:
            log_message(f"⚠ Elevation rendering failed: {e}", callback)
            import traceback
            traceback.print_exc()


    # Layer 2: Roads with hierarchy coloring
    log_message("Applying road hierarchy colors...", callback, 80)
    edge_colors = get_edge_colors_by_type(g_proj, theme)
    edge_widths = get_edge_widths_by_type(g_proj)

    # Determine cropping limits to maintain the poster aspect ratio
    crop_xlim, crop_ylim = get_crop_limits(g_proj, point, fig, span)

    # Draw Buildings (Layer 2)
    if buildings is not None and not buildings.empty:
        log_message("Rendering building footprints...", callback, 88)
        try:
            buildings_proj = ox.projection.project_gdf(buildings)
            # Render shadow — subtle, very thin
            shadow_offset = 0.02 * (span / width)
            buildings_proj.translate(xoff=shadow_offset, yoff=-shadow_offset).plot(
                ax=ax, facecolor=theme.get('text', '#000000'), alpha=0.08, edgecolor='none', linewidth=0, zorder=2
            )
            # Render building fill — no border
            buildings_proj.plot(ax=ax, facecolor=theme.get('building', theme['road_residential']), edgecolor='none', linewidth=0, zorder=3)
        except Exception as e:
            log_message(f"⚠ Building rendering failed: {e}", callback)

    # Plot the projected graph and then apply the cropped limits
    log_message("Finalizing map rendering...", callback, 92)
    ox.plot_graph(
        g_proj, ax=ax, bgcolor=theme['bg'],
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        show=False,
        close=False,
    )
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
        ax.text(0.5, y_city, spaced_city, transform=ax.transAxes, color=theme["text"], ha="center", fontproperties=font_main_adjusted, zorder=11)
        ax.text(0.5, y_country, display_country.upper(), transform=ax.transAxes, color=theme["text"], ha="center", fontproperties=font_sub, zorder=11)
        ax.plot([0.4, 0.6], [y_sep, y_sep], transform=ax.transAxes, color=theme["text"], linewidth=1 * scale_factor, zorder=11)

    if not no_coords:
        lat, lon = point
        coords_text = f"{lat:.4f}° N / {lon:.4f}° E" if lat >= 0 else f"{abs(lat):.4f}° S / {lon:.4f}° E"
        if lon < 0: coords_text = coords_text.replace("E", "W")
        ax.text(0.5, y_coords, coords_text, transform=ax.transAxes, color=theme["text"], alpha=0.7, ha="center", fontproperties=font_coords, zorder=11)

    ax.text(0.98, 0.02, "aesthetimap.rastiegaiev.com", transform=ax.transAxes, color=theme["text"], alpha=0.5, ha="right", va="bottom", fontproperties=font_attr, zorder=11)

    # 5. Save
    log_message(f"Saving to {output_file}...", callback, 95)
    fmt = output_format.lower()
    save_kwargs = dict(facecolor=theme["bg"], bbox_inches="tight", pad_inches=0.05)
    if fmt == "png": save_kwargs["dpi"] = 300
    fig.savefig(output_file, format=fmt, **save_kwargs)
    plt.close(fig)
    log_message(f"✓ Success! Poster saved: {output_file}", callback, 100)


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
    callback: Optional[Callable[[str, Optional[int]], None]] = None,
):
    """Entry point for library calls."""
    custom_fonts = load_fonts(font_family) if font_family else None
    if latitude and longitude:
        coords = (parse(latitude), parse(longitude))
    else:
        coords = get_coordinates(city, country)

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
        show_contours=show_contours, callback=callback
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
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  python create_map_poster.py -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  python create_map_poster.py -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline

  # Radial patterns
  python create_map_poster.py -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  python create_map_poster.py -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads

  # Organic old cities
  python create_map_poster.py -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  python create_map_poster.py -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout

  # Coastal cities
  python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula

  # River cities
  python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
  python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split

  # List themes
  python create_map_poster.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --country-label   Override country text displayed on poster
  --theme, -t       Theme name (default: terracotta)
  --all-themes      Generate posters for all themes
  --distance, -d    Map radius in meters (default: 18000)
  --list-themes     List all available themes

Distance guide:
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_map_poster.py --city "New York" --country "USA"
  python create_map_poster.py --city "New York" --country "USA" -l 40.776676 -73.971321 --theme neon_cyberpunk
  python create_map_poster.py --city Tokyo --country Japan --theme midnight_blue
  python create_map_poster.py --city Paris --country France --theme noir --distance 15000
  python create_map_poster.py --list-themes
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
                args.width,
                args.height,
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
            )

        print("\n" + "=" * 50)
        print("✓ Poster generation complete!")
        print("=" * 50)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
