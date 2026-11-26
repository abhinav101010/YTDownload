"""Download state machine, progress parsing and human formatting helpers.

Progress dictionaries produced by yt-dlp may omit almost any field (unknown
total size, no speed yet, estimated totals, …).  :func:`parse_progress` and
:func:`parse_postprocessor` therefore never assume a key exists.

Status transitions are explicit: :func:`transition` raises
:class:`InvalidTransition` when a move is not allowed, so illegal state
changes surface as bugs instead of silently corrupting the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Optional


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def coerce(cls, value: "str | DownloadStatus | None") -> Optional["DownloadStatus"]:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            return None
        for member in cls:
            if member.value == value.lower().strip():
                return member
        return None


TERMINAL_STATUSES = frozenset(
    {DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED}
)
ACTIVE_STATUSES = frozenset(
    {DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING}
)

# Explicit, honest state machine.
_ALLOWED_TRANSITIONS: dict[DownloadStatus, frozenset[DownloadStatus]] = {
    DownloadStatus.QUEUED: frozenset(
        {DownloadStatus.DOWNLOADING, DownloadStatus.CANCELLED, DownloadStatus.FAILED}
    ),
    DownloadStatus.DOWNLOADING: frozenset(
        {
            DownloadStatus.PROCESSING,
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
        }
    ),
    DownloadStatus.PROCESSING: frozenset(
        {DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED}
    ),
    # Retry re-queues the same task in place (keeps UI row identity stable).
    DownloadStatus.FAILED: frozenset({DownloadStatus.QUEUED}),
    DownloadStatus.CANCELLED: frozenset({DownloadStatus.QUEUED}),
    DownloadStatus.COMPLETED: frozenset(),
}


class InvalidTransition(RuntimeError):
    def __init__(self, current: DownloadStatus, target: DownloadStatus) -> None:
        super().__init__(f"Illegal download state transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: DownloadStatus, target: DownloadStatus) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


def transition(current: DownloadStatus, target: DownloadStatus) -> DownloadStatus:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    return target


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProgressInfo:
    """Normalized progress snapshot. Every numeric field may be ``None``."""

    status: DownloadStatus = DownloadStatus.QUEUED
    percent: Optional[float] = None
    downloaded_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    total_is_estimate: bool = False
    speed: Optional[float] = None  # bytes per second
    eta: Optional[int] = None  # seconds
    stage: Optional[str] = None  # human-readable stage, e.g. "Merging streams"

    @property
    def has_total(self) -> bool:
        return bool(self.total_bytes)


def _as_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> Optional[int]:
    number = _as_number(value)
    if number is None or number < 0:
        return None
    return int(number)


def _percent_from_string(raw: Any) -> Optional[float]:
    """Parse yt-dlp's pre-formatted ``_percent_str`` like ``' 42.5%'``."""
    if not isinstance(raw, str):
        return None
    number = _as_number(raw.strip().rstrip("%").strip())
    if number is None:
        return None
    return max(0.0, min(100.0, number))


def parse_progress(data: dict[str, Any], *, stage: Optional[str] = None) -> ProgressInfo:
    """Build a :class:`ProgressInfo` from a yt-dlp progress hook payload.

    Tolerates missing/invalid fields: unknown totals yield ``percent=None``
    (the UI then shows an indeterminate bar rather than a fake number).
    """
    if not isinstance(data, dict):
        return ProgressInfo(status=DownloadStatus.DOWNLOADING, stage=stage)

    downloaded = _as_int(data.get("downloaded_bytes"))
    total = _as_int(data.get("total_bytes"))
    is_estimate = False
    if total is None:
        total = _as_int(data.get("total_bytes_estimate"))
        is_estimate = total is not None

    percent: Optional[float] = None
    if total:
        if downloaded is not None:
            percent = max(0.0, min(100.0, downloaded * 100.0 / total))
        else:
            # yt-dlp's pre-formatted string is the only other signal.
            percent = _percent_from_string(data.get("_percent_str"))

    return ProgressInfo(
        status=DownloadStatus.DOWNLOADING,
        percent=percent,
        downloaded_bytes=downloaded,
        total_bytes=total,
        total_is_estimate=is_estimate,
        speed=_as_number(data.get("speed")),
        eta=_as_int(data.get("eta")),
        stage=stage,
    )


_PP_STAGE_LABELS = {
    "Merger": "Merging audio and video…",
    "FFmpegExtractAudio": "Converting audio…",
    "FFmpegVideoConvertor": "Converting video…",
    "FFmpegMetadata": "Writing metadata…",
    "MoveFiles": "Moving file…",
}


def parse_postprocessor(data: dict[str, Any]) -> ProgressInfo:
    """Build a :class:`ProgressInfo` for a postprocessor hook payload."""
    if not isinstance(data, dict):
        return ProgressInfo(status=DownloadStatus.PROCESSING)
    pp_name = str(data.get("postprocessor") or "")
    label = _PP_STAGE_LABELS.get(pp_name)
    if label is None and pp_name:
        label = f"Processing ({pp_name.replace('FFmpeg', '').replace('PP', '')})…"
    return ProgressInfo(status=DownloadStatus.PROCESSING, stage=label or "Processing…")


# --------------------------------------------------------------------------- #
# Task snapshot shared between services and UI
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TaskSnapshot:
    """Immutable view of one queue entry, safe to pass across threads."""

    id: str
    url: str
    title: str
    thumbnail_url: Optional[str]
    quality_label: str
    format_label: str
    mode: str  # "video" | "audio"
    destination: str
    status: DownloadStatus
    progress: ProgressInfo = field(default_factory=ProgressInfo)
    error: Optional[str] = None
    file_path: Optional[str] = None
    created_at: str = ""
    history_id: Optional[int] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


# --------------------------------------------------------------------------- #
# Formatting helpers (shared by the UI)
# --------------------------------------------------------------------------- #


def format_bytes(num_bytes: Optional[float]) -> str:
    if num_bytes is None:
        return "—"
    value = float(num_bytes)
    if value < 0:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while value >= 1024.0 and index < len(units) - 1:
        value /= 1024.0
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.1f} {units[index]}"


def format_speed(bytes_per_second: Optional[float]) -> str:
    if bytes_per_second is None or bytes_per_second <= 0:
        return "—"
    return f"{format_bytes(bytes_per_second)}/s"


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "—"
    return format_duration(int(seconds))


def format_duration(seconds: Optional[float]) -> str:
    """Format seconds as ``m:ss`` or ``h:mm:ss``. Returns ``—`` when unknown."""
    if seconds is None:
        return "—"
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    if total < 0:
        return "—"
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def statuses_in(values: Iterable[DownloadStatus | str]) -> set[DownloadStatus]:
    """Coerce an iterable of strings/statuses into a set of statuses."""
    out: set[DownloadStatus] = set()
    for value in values:
        status = DownloadStatus.coerce(value)
        if status is not None:
            out.add(status)
    return out
