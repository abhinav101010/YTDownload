"""Visual system: colours, spacing and fonts.

Colour values are ``(light, dark)`` tuples as CustomTkinter expects.
Fonts are created lazily (``CTkFont`` needs a Tk root) and shared.
"""

from __future__ import annotations

from functools import lru_cache

import customtkinter as ctk

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #

PAGE_BG = ("#eef1f6", "#0e1116")
SIDEBAR_BG = ("#e5e9f0", "#141920")
CARD = ("#ffffff", "#171c24")
CARD_HOVER = ("#f2f5fa", "#1c222c")
INPUT_BG = ("#f3f5f9", "#12161d")
BORDER = ("#d9dfe8", "#242b36")

TEXT = ("#1b2430", "#e8ecf3")
TEXT_DIM = ("#5f6a7a", "#9aa5b5")
TEXT_FAINT = ("#8b95a5", "#6f7a8a")

ACCENT = ("#2f7fd0", "#4a9fe0")
DANGER = ("#d64545", "#f06a6a")
SUCCESS = ("#1f9d55", "#3ec27a")
WARNING = ("#b7791f", "#e3aa3f")
VIOLET = ("#6d5dd3", "#9d8df1")
# Soft tints for subtle backgrounds (no alpha hex — Tk 8.6 rejects it).
DANGER_SOFT = ("#f9e7e7", "#35222a")

STATUS_COLORS: dict[str, tuple[str, str]] = {
    "queued": TEXT_DIM,
    "downloading": ACCENT,
    "processing": VIOLET,
    "completed": SUCCESS,
    "failed": DANGER,
    "cancelled": TEXT_FAINT,
    "interrupted": WARNING,
}

STATUS_TEXT: dict[str, str] = {
    "queued": "Queued",
    "downloading": "Downloading",
    "processing": "Processing",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "interrupted": "Interrupted",
}

# Solid tint backgrounds for status pills (Tk 8.6 alpha-hex is unreliable).
STATUS_PILL: dict[str, tuple[str, str]] = {
    "queued": ("#e9edf3", "#232a35"),
    "downloading": ("#e2edf9", "#1b2c3f"),
    "processing": ("#ece9fa", "#282342"),
    "completed": ("#e2f4ea", "#1b3226"),
    "failed": ("#fae6e6", "#3a1f22"),
    "cancelled": ("#eceef2", "#21242b"),
    "interrupted": ("#f8efdc", "#332915"),
}

# --------------------------------------------------------------------------- #
# Spacing scale
# --------------------------------------------------------------------------- #

SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24

RADIUS_CARD = 14
RADIUS_BUTTON = 9

SIDEBAR_WIDTH = 226
PAGE_PAD = 26

# --------------------------------------------------------------------------- #
# Fonts (lazy — CTkFont requires a default Tk root)
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=None)
def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    """Shared CTkFont (must be called after the Tk root exists)."""
    return ctk.CTkFont(size=size, weight=weight)


def status_color(status: str) -> tuple[str, str]:
    return STATUS_COLORS.get(status, TEXT_DIM)


def status_text(status: str) -> str:
    return STATUS_TEXT.get(status, status.title())
