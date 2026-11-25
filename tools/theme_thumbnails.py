#!/usr/bin/env python3
"""Draw the theme thumbnails of the web form: frontend/public/themes/<theme>.webp.

Run from the repository root after adding or changing a theme:
    python tools/theme_thumbnails.py [theme ...]
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import renderer  # noqa: E402
from PIL import Image  # noqa: E402

OUTPUT = ROOT / "frontend" / "public" / "themes"
draw_lines = renderer.draw_lines


def draw_bold_lines(ax, lines, color, width, zorder):
    """Print-weight roads vanish at thumbnail size."""
    draw_lines(ax, lines, color, width * (3.2 if width > 0.12 else 2.0), zorder)


def main():
    renderer.draw_lines = draw_bold_lines
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        renderer.POSTERS_DIR = directory
        for theme in sys.argv[1:] or renderer.get_available_themes():
            poster = renderer.run_generator(
                city="Barcelona", country="Spain", latitude="41.3790", longitude="2.1780", theme=theme,
                span=4600, width=3, height=4, no_title=True, no_coords=True)
            image = Image.open(poster).convert("RGB")
            width, height = image.size
            # Cut off the outer padding and the credit line; the page carries the credit.
            image = image.crop((int(width * 0.04), int(height * 0.03), int(width * 0.96), int(height * 0.93)))
            image.resize((168, 224), Image.LANCZOS).save(OUTPUT / f"{theme}.webp", quality=82, method=6)


if __name__ == "__main__":
    main()
