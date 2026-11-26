#!/usr/bin/env python3
"""Generate the line-art PNG icons used by the UI.

Run from the project root:

    .venv/bin/python tools/generate_icons.py

Every icon is drawn twice:

* ``<name>.png``       – white ink, used in the dark appearance.
* ``<name>_light.png`` – dark ink, used in the light appearance.

The UI loads them through :mod:`ui.icons` (CTkImage dark/light pair).
The only coloured asset is the app logo.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 64
STROKE = 5
WHITE = (255, 255, 255, 255)
INK = (42, 46, 55, 255)

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"


def new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def stroke_line(draw: ImageDraw.ImageDraw, points, fill, width=STROKE):
    """Polyline with round caps/joints."""
    draw.line(points, fill=fill, width=width, joint="curve")
    radius = width / 2
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def stroke_circle(draw, cx, cy, radius, fill, width=STROKE):
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    draw.ellipse(bbox, outline=fill, width=width)


def arc_points(cx, cy, radius, start_deg, end_deg, steps=24):
    points = []
    for i in range(steps + 1):
        t = math.radians(start_deg + (end_deg - start_deg) * i / steps)
        points.append((cx + radius * math.cos(t), cy + radius * math.sin(t)))
    return points


# --------------------------------------------------------------------------- #
# Icon bodies — each receives a draw handle and an ink colour
# --------------------------------------------------------------------------- #


def icon_home(draw, c):
    stroke_line(draw, [(12, 32), (32, 14), (52, 32)], c)
    stroke_line(draw, [(18, 30), (18, 52), (46, 52), (46, 30)], c)
    stroke_line(draw, [(27, 52), (27, 40), (37, 40), (37, 52)], c)


def icon_download(draw, c):
    stroke_line(draw, [(32, 12), (32, 38)], c)
    stroke_line(draw, [(21, 28), (32, 40), (43, 28)], c)
    stroke_line(draw, [(14, 44), (14, 52), (50, 52), (50, 44)], c)


def icon_history(draw, c):
    stroke_circle(draw, 32, 33, 19, c)
    stroke_line(draw, [(32, 33), (32, 22)], c)
    stroke_line(draw, [(32, 33), (41, 39)], c)
    stroke_line(draw, [(10, 18), (18, 10), (28, 12)], c)


def icon_settings(draw, c):
    stroke_circle(draw, 32, 32, 8, c)
    stroke_circle(draw, 32, 32, 18, c, width=3)
    for angle in range(0, 360, 45):
        t = math.radians(angle)
        x1, y1 = 32 + 14 * math.cos(t), 32 + 14 * math.sin(t)
        x2, y2 = 32 + 22 * math.cos(t), 32 + 22 * math.sin(t)
        stroke_line(draw, [(x1, y1), (x2, y2)], c, width=4)


def icon_folder(draw, c):
    stroke_line(draw, [(12, 24), (12, 52), (52, 52), (52, 28), (28, 28), (24, 24), (12, 24)], c)


def icon_open(draw, c):
    stroke_line(draw, [(40, 14), (52, 14), (52, 26)], c)
    stroke_line(draw, [(52, 14), (30, 36)], c)
    stroke_line(draw, [(40, 32), (40, 52), (14, 52), (14, 20), (32, 20)], c)


def icon_retry(draw, c):
    stroke_line(draw, arc_points(32, 33, 17, -60, 210), c)
    stroke_line(draw, [(37, 12), (44, 20), (34, 25)], c)


def icon_cancel(draw, c):
    stroke_circle(draw, 32, 32, 19, c)
    stroke_line(draw, [(24, 24), (40, 40)], c)
    stroke_line(draw, [(40, 24), (24, 40)], c)


def icon_trash(draw, c):
    stroke_line(draw, [(14, 19), (50, 19)], c)
    stroke_line(draw, [(26, 12), (38, 12)], c)
    stroke_line(draw, [(19, 19), (22, 52), (42, 52), (45, 19)], c)
    stroke_line(draw, [(29, 27), (29, 44)], c)
    stroke_line(draw, [(35, 27), (35, 44)], c)


def icon_paste(draw, c):
    stroke_line(draw, [(20, 18), (20, 52), (44, 52), (44, 18), (20, 18)], c)
    stroke_line(draw, [(26, 14), (38, 14), (38, 21), (26, 21), (26, 14)], c)
    stroke_line(draw, [(26, 31), (38, 31)], c)
    stroke_line(draw, [(26, 40), (38, 40)], c)


def icon_clear(draw, c):
    stroke_line(draw, [(20, 20), (44, 44)], c)
    stroke_line(draw, [(44, 20), (20, 44)], c)
    stroke_line(draw, [(12, 52), (52, 52)], c)


def icon_search(draw, c):
    stroke_circle(draw, 27, 27, 14, c)
    stroke_line(draw, [(37, 37), (50, 50)], c)


def icon_film(draw, c):
    stroke_line(draw, [(12, 20), (52, 20), (52, 44), (12, 44), (12, 20)], c)
    stroke_line(draw, [(20, 20), (20, 44)], c)
    stroke_line(draw, [(44, 20), (44, 44)], c)
    stroke_line(draw, [(12, 32), (20, 32)], c)
    stroke_line(draw, [(44, 32), (52, 32)], c)


def icon_music(draw, c):
    stroke_line(draw, [(26, 46), (26, 18), (42, 22)], c)
    stroke_line(draw, [(26, 24), (42, 28)], c)
    stroke_circle(draw, 20, 47, 6, c)


def icon_link(draw, c):
    stroke_line(draw, arc_points(24, 40, 10, 0, 360, 32), c)
    stroke_line(draw, arc_points(42, 26, 10, 0, 360, 32), c)
    stroke_line(draw, [(31, 33), (35, 29)], c)


def icon_check_circle(draw, c):
    stroke_circle(draw, 32, 32, 19, c)
    stroke_line(draw, [(23, 33), (29, 40), (42, 24)], c)


def icon_alert(draw, c):
    stroke_line(draw, [(32, 12), (54, 50), (10, 50), (32, 12)], c)
    stroke_line(draw, [(32, 26), (32, 40)], c)
    stroke_line(draw, [(32, 46), (32, 46.1)], c)


def icon_info(draw, c):
    stroke_circle(draw, 32, 32, 19, c)
    stroke_line(draw, [(32, 29), (32, 45)], c)
    stroke_line(draw, [(32, 21), (32, 21.1)], c)


def icon_image(draw, c):
    stroke_line(draw, [(12, 18), (52, 18), (52, 46), (12, 46), (12, 18)], c)
    stroke_circle(draw, 41, 27, 3, c)
    stroke_line(draw, [(14, 44), (27, 31), (40, 44)], c)


def icon_link_off(draw, c):
    """Unsupported/broken source marker."""
    stroke_line(draw, arc_points(24, 40, 10, -140, 140, 24), c)
    stroke_line(draw, arc_points(42, 26, 10, 40, 320, 24), c)
    stroke_line(draw, [(20, 18), (46, 48)], c)


ICONS = {
    "home": icon_home,
    "download": icon_download,
    "history": icon_history,
    "settings": icon_settings,
    "folder": icon_folder,
    "open": icon_open,
    "retry": icon_retry,
    "cancel": icon_cancel,
    "trash": icon_trash,
    "paste": icon_paste,
    "clear": icon_clear,
    "search": icon_search,
    "film": icon_film,
    "music": icon_music,
    "link": icon_link,
    "check": icon_check_circle,
    "alert": icon_alert,
    "info": icon_info,
    "image": icon_image,
    "link_off": icon_link_off,
}


def draw_logo() -> Image.Image:
    """Coloured app logo: rounded square + play triangle."""
    image, draw = new_canvas()
    draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill=(227, 49, 49, 255))
    draw.polygon([(25, 20), (25, 44), (47, 32)], fill=(255, 255, 255, 255))
    return image


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in ICONS.items():
        for suffix, ink in (("", WHITE), ("_light", INK)):
            image, draw = new_canvas()
            body(draw, ink)
            path = ASSET_DIR / f"{name}{suffix}.png"
            image.save(path)
    draw_logo().save(ASSET_DIR / "logo.png")
    # Simple placeholder for missing thumbnails (greyscale).
    image, draw = new_canvas()
    icon_image(draw, (120, 126, 138, 255))
    image.save(ASSET_DIR / "placeholder.png")
    print(f"Wrote {len(ICONS) * 2 + 2} icons to {ASSET_DIR}")


if __name__ == "__main__":
    main()
