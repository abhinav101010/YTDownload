"""Icon loading: pairs of PNGs rendered as themed CTkImage objects.

``<name>.png`` is the white-ink (dark appearance) variant, ``<name>_light.png``
the dark-ink (light appearance) variant. Images are cached per name+size.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import customtkinter as ctk
from PIL import Image

ICONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"


@lru_cache(maxsize=None)
def _pair(name: str) -> tuple[Image.Image, Image.Image]:
    dark = Image.open(ICONS_DIR / f"{name}.png").convert("RGBA")
    light = Image.open(ICONS_DIR / f"{name}_light.png").convert("RGBA")
    return dark, light


@lru_cache(maxsize=None)
def icon(name: str, size: int = 18) -> ctk.CTkImage:
    """Themed icon image (white ink on dark, dark ink on light)."""
    dark, light = _pair(name)
    return ctk.CTkImage(dark_image=dark, light_image=light, size=(size, size))


@lru_cache(maxsize=None)
def plain(name: str, size: int = 18) -> ctk.CTkImage:
    """Single-variant image (same art in both appearances)."""
    image = Image.open(ICONS_DIR / f"{name}.png").convert("RGBA")
    return ctk.CTkImage(dark_image=image, light_image=image, size=(size, size))


def logo(size: int = 36) -> ctk.CTkImage:
    return plain("logo", size)


def placeholder(size: int = 48) -> ctk.CTkImage:
    return plain("placeholder", size)
