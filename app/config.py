"""Application configuration: paths, constants, defaults and logging.

All filesystem locations are derived at call time so that tests can point the
application at temporary directories without patching module globals.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "YT Downloader"
APP_SLUG = "YTDownloader"
APP_VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# Data locations
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def app_data_dir() -> Path:
    """Return the per-user writable data directory (created on demand).

    Set the ``YTDL_APP_DATA_DIR`` environment variable to override (used by
    tests and portable installs).
    """
    override = os.environ.get("YTDL_APP_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_SLUG
    elif os.name == "nt":  # pragma: no cover - windows only
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_SLUG
    else:  # pragma: no cover - linux/bsd only
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_SLUG
    base.mkdir(parents=True, exist_ok=True)
    return base


@lru_cache(maxsize=1)
def database_path() -> Path:
    return app_data_dir() / "yt_downloader.db"


@lru_cache(maxsize=1)
def log_file_path() -> Path:
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "app.log"


@lru_cache(maxsize=1)
def thumbnail_cache_dir() -> Path:
    cache_dir = app_data_dir() / "cache" / "thumbnails"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def default_download_dir() -> Path:
    """Default destination for downloaded media (user-writable)."""
    return Path.home() / "Downloads" / APP_NAME.replace(" ", "")


# --------------------------------------------------------------------------- #
# Settings defaults
# --------------------------------------------------------------------------- #

APPEARANCE_MODES = ("Dark", "Light", "System")
ACCENT_THEMES = ("blue", "green", "dark-blue")
DOWNLOAD_MODES = ("video", "audio")
VIDEO_CONTAINERS = ("mp4", "mkv", "webm")
MAX_CONCURRENT_CHOICES = (1, 2, 3, 4)

DEFAULT_SETTINGS: dict[str, str] = {
    "download_dir": str(default_download_dir()),
    "default_mode": "video",
    "preferred_quality": "best",
    "video_container": "mp4",
    "appearance": "Dark",
    "accent_theme": "blue",
    "max_concurrent": "1",
    "ffmpeg_path": "",
    "check_updates_on_start": "1",
}

# Keys whose values are validated on load so a hand-edited database can never
# break startup.
_ENUM_SETTINGS: dict[str, tuple[str, ...]] = {
    "default_mode": DOWNLOAD_MODES,
    "video_container": VIDEO_CONTAINERS,
    "appearance": APPEARANCE_MODES,
    "accent_theme": ACCENT_THEMES,
    "max_concurrent": tuple(str(n) for n in MAX_CONCURRENT_CHOICES),
}

_BOOL_SETTINGS = ("check_updates_on_start",)

MAX_URL_LENGTH = 2048


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(verbose: bool = False) -> None:
    """Configure rotating file logging plus a quiet console handler."""
    root = logging.getLogger()
    if root.handlers:  # already configured (e.g. repeated startup in tests)
        return

    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file_path(), maxBytes=512 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(console)

    # Third-party libraries are chatty at DEBUG level; keep them out of the
    # file log unless explicitly requested.
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)


def sanitize_settings(raw: dict[str, str]) -> dict[str, str]:
    """Merge persisted settings over defaults, discarding invalid values."""
    cleaned: dict[str, str] = {}
    for key, default in DEFAULT_SETTINGS.items():
        value = raw.get(key, default)
        if not isinstance(value, str):
            value = default
        if key in _ENUM_SETTINGS and value not in _ENUM_SETTINGS[key]:
            value = default
        if key in _BOOL_SETTINGS and value not in ("0", "1"):
            value = default
        if key == "max_concurrent" and not value.isdigit():
            value = default
        if key == "download_dir" and not value.strip():
            # A blank folder would make every download fail preflight.
            value = default
        cleaned[key] = value
    return cleaned
