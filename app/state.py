"""Shared application state: settings model and the worker→UI event bus.

Background workers must never touch Tk widgets.  They post :class:`Event`
objects to the :class:`EventBus`; the Tk main loop drains the bus with
``root.after(...)`` and applies updates on the main thread.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, fields
from typing import Any

from app import config

# Event kinds posted by background workers.
EVENT_METADATA = "metadata"      # payload: (token, MediaInfo | None, error: str | None)
EVENT_THUMBNAIL = "thumbnail"    # payload: (url, PIL.Image.Image | None)
EVENT_DOWNLOAD = "download"      # payload: downloader.progress.TaskSnapshot
EVENT_TOAST = "toast"            # payload: (level, message)
EVENT_SYSTEM = "system"          # payload: dict with ad-hoc system info (e.g. update check)

TOAST_INFO = "info"
TOAST_SUCCESS = "success"
TOAST_WARNING = "warning"
TOAST_ERROR = "error"


@dataclass(frozen=True)
class AppSettings:
    """Validated, typed snapshot of persisted user settings."""

    download_dir: str
    default_mode: str
    preferred_quality: str
    video_container: str
    appearance: str
    accent_theme: str
    max_concurrent: int
    ffmpeg_path: str
    check_updates_on_start: bool

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppSettings":
        cleaned = config.sanitize_settings({k: str(v) for k, v in (raw or {}).items()})
        return cls(
            download_dir=cleaned["download_dir"],
            default_mode=cleaned["default_mode"],
            preferred_quality=cleaned["preferred_quality"],
            video_container=cleaned["video_container"],
            appearance=cleaned["appearance"],
            accent_theme=cleaned["accent_theme"],
            max_concurrent=int(cleaned["max_concurrent"]),
            ffmpeg_path=cleaned["ffmpeg_path"],
            check_updates_on_start=cleaned["check_updates_on_start"] == "1",
        )

    def to_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, bool):
                out[f.name] = "1" if value else "0"
            else:
                out[f.name] = str(value)
        return out

    def replace(self, **changes: Any) -> "AppSettings":
        from dataclasses import replace as _replace

        return _replace(self, **changes)


@dataclass(frozen=True)
class Event:
    kind: str
    payload: Any = None
    token: Any = None


class EventBus:
    """Thread-safe one-way queue from worker threads to the Tk main loop."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[Event]" = queue.Queue()

    def post(self, kind: str, payload: Any = None, token: Any = None) -> None:
        self._queue.put(Event(kind=kind, payload=payload, token=token))

    def drain(self, max_items: int = 64) -> list[Event]:
        """Return up to ``max_items`` pending events without blocking."""
        events: list[Event] = []
        for _ in range(max_items):
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def qsize(self) -> int:
        return self._queue.qsize()
