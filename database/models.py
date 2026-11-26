"""Data models for persisted records.

Kept free of I/O so they can be constructed and asserted in unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# History statuses.  These deliberately mirror downloader.progress values,
# plus "interrupted" for downloads that were cut short by an app shutdown.
HISTORY_STATUSES = (
    "downloading",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)

STATUS_LABELS = {
    "downloading": "Downloading",
    "queued": "Queued",
    "processing": "Processing",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "interrupted": "Interrupted",
}


@dataclass(frozen=True)
class HistoryRecord:
    id: int
    source_url: str
    title: str
    thumbnail_url: Optional[str]
    quality: Optional[str]
    output_format: Optional[str]
    destination_path: Optional[str]
    status: str
    created_at: str
    completed_at: Optional[str] = None
    error_message: Optional[str] = None

    def file_state(self) -> str:
        """Return ``"available"``, ``"missing"`` or ``"unknown"``.

        A history entry whose file no longer exists must never be presented
        as an available download.
        """
        if self.status != "completed":
            return "unknown"
        if not self.destination_path:
            return "missing"
        try:
            return "available" if Path(self.destination_path).is_file() else "missing"
        except OSError:
            return "missing"

    @property
    def file_available(self) -> bool:
        return self.file_state() == "available"

    @property
    def is_stale(self) -> bool:
        return self.status == "completed" and not self.file_available

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.title())

    @property
    def created_at_display(self) -> str:
        return _display_time(self.created_at)


def _display_time(iso_value: Optional[str]) -> str:
    if not iso_value:
        return "—"
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        return iso_value
    return parsed.strftime("%Y-%m-%d %H:%M")


def now_iso() -> str:
    """Local-time ISO timestamp with seconds precision."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
