"""Filesystem helpers: safe names, destination validation, opening files.

Rules enforced here:

* Every path is a :class:`pathlib.Path`; no ``shell=True`` anywhere.
* Metadata-derived names are untrusted: control characters, path separators
  and reserved names are stripped by :func:`sanitize_filename`.
* Destinations are verified writable before a download starts.
* Cleanup only ever deletes partial files (``*.part`` / ``*.ytdl``) that
  belong to a specific video id — never unrelated user files.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Iterable, Optional


class FileServiceError(RuntimeError):
    """Base class for filesystem failures shown to the user."""


class DestinationError(FileServiceError):
    """The chosen download destination cannot be used."""


class InsufficientSpaceError(FileServiceError):
    """Not enough free disk space for the expected download size."""

    def __init__(self, free_bytes: int, required_bytes: int) -> None:
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
        super().__init__(
            f"Not enough free disk space: about {format_size(required_bytes)} needed, "
            f"{format_size(free_bytes)} available."
        )


class FileActionError(FileServiceError):
    """Opening a file/folder in the OS file manager failed."""


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #

_FORBIDDEN_CHARS = set('<>:"/\\|?*')
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: Optional[str], max_length: int = 180) -> str:
    """Return a filesystem-safe file name (no directory components).

    Defensive by design: even though yt-dlp sanitizes its own output
    template, anything this application constructs itself (fallback paths,
    cache entries, user-visible labels) goes through here first.
    """
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = _CONTROL_RE.sub("", text)
    text = text.replace("\u202e", "").replace("\u202a", "")  # bidi overrides
    cleaned = "".join("_" if (ch in _FORBIDDEN_CHARS or ord(ch) < 32) else ch for ch in text)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    # Windows-compatible: no trailing dots/spaces.
    cleaned = cleaned.rstrip(". ")
    if not cleaned:
        return "untitled"

    stem = cleaned.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"

    if len(cleaned) > max_length:
        root, dot, ext = cleaned.rpartition(".")
        if dot and 0 < len(ext) <= 10 and len(root) > 8:
            keep = max_length - len(ext) - 1
            cleaned = f"{root[:keep].rstrip('. ')}.{ext}"
        else:
            cleaned = cleaned[:max_length].rstrip(". ")
    return cleaned or "untitled"


def format_size(num_bytes: Optional[float]) -> str:
    if num_bytes is None:
        return "unknown size"
    value = float(num_bytes)
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while value >= 1024.0 and index < len(units) - 1:
        value /= 1024.0
        index += 1
    return f"{value:.1f} {units[index]}" if index else f"{int(value)} B"


# --------------------------------------------------------------------------- #
# Destinations
# --------------------------------------------------------------------------- #


def _is_writable(directory: Path) -> bool:
    probe = directory / f".yt-download-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def validate_destination(path: Optional[str | Path], *, create: bool = False) -> Path:
    """Validate (and optionally create) a download destination directory.

    Raises :class:`DestinationError` with a user-readable explanation.
    """
    if path is None or str(path).strip() == "":
        raise DestinationError("No download folder has been chosen.")

    candidate = Path(str(path)).expanduser()
    if not candidate.is_absolute():
        raise DestinationError(f"The download folder must be an absolute path: {candidate}")

    if candidate.exists() and not candidate.is_dir():
        raise DestinationError(f"{candidate} exists but is not a folder.")

    if not candidate.exists():
        if not create:
            raise DestinationError(f"The folder {candidate} does not exist yet.")
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DestinationError(f"Could not create {candidate}: {exc}") from exc

    if not _is_writable(candidate):
        raise DestinationError(f"The folder {candidate} is not writable.")
    return candidate


def ensure_free_space(
    directory: Path, required_bytes: Optional[int], *, safety_margin: int = 64 * 1024 * 1024
) -> None:
    """Raise :class:`InsufficientSpaceError` when the estimate won't fit.

    Unknown sizes are allowed through — the OS/disk errors are reported later
    if space really runs out.
    """
    if not required_bytes or required_bytes <= 0:
        return
    try:
        usage = shutil.disk_usage(directory)
    except OSError:
        return  # cannot determine; let the download attempt proceed
    needed = int(required_bytes) + safety_margin
    if usage.free < needed:
        raise InsufficientSpaceError(free_bytes=int(usage.free), required_bytes=needed)


def unique_path(path: Path) -> Path:
    """Return ``path`` if free, otherwise ``name (1).ext``, ``name (2).ext``…"""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for counter in range(1, 10_000):
        candidate = path.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileServiceError(f"Could not find a free name for {path.name}")


def cleanup_partial_files(directory: Path, video_id: Optional[str]) -> list[Path]:
    """Delete leftover ``*.part``/``*.ytdl`` files belonging to ``video_id``.

    Only partial files are touched, only inside ``directory``, and only when
    the video id marker ``[<id>]`` (written by our output template) is in the
    file name — so unrelated user files can never be removed.
    """
    removed: list[Path] = []
    if not directory.is_dir() or not video_id:
        return removed
    marker = f"[{video_id}]"
    for suffix in ("*.part", "*.ytdl"):
        for candidate in directory.glob(suffix):
            if not candidate.is_file() or marker not in candidate.name:
                continue
            try:
                candidate.unlink()
                removed.append(candidate)
            except OSError:
                continue
    return removed


# --------------------------------------------------------------------------- #
# Opening in the OS
# --------------------------------------------------------------------------- #


def _run_quiet(args: list[str]) -> None:
    subprocess.run(
        args,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        timeout=20,
    )


def open_file(path: Optional[str | Path]) -> None:
    """Open a file with the default application."""
    if path is None:
        raise FileActionError("There is no file to open.")
    target = Path(path)
    if not target.is_file():
        raise FileActionError(f"The file no longer exists: {target.name}")
    system = platform.system()
    try:
        if system == "Darwin":
            _run_quiet(["open", str(target)])
        elif system == "Windows":  # pragma: no cover - windows only
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:  # pragma: no cover - linux/bsd only
            _run_quiet(["xdg-open", str(target)])
    except (OSError, subprocess.SubprocessError) as exc:
        raise FileActionError(f"Could not open {target.name}: {exc}") from exc


def open_containing_folder(path: Optional[str | Path]) -> None:
    """Reveal the file in the file manager (selected, when supported)."""
    if path is None:
        raise FileActionError("There is no folder to reveal.")
    target = Path(path)
    folder = target if target.is_dir() else target.parent
    if not folder.is_dir():
        raise FileActionError("The containing folder no longer exists.")
    system = platform.system()
    try:
        if system == "Darwin":
            if target.is_file():
                _run_quiet(["open", "-R", str(target)])
            else:
                _run_quiet(["open", str(folder)])
        elif system == "Windows":  # pragma: no cover - windows only
            if target.is_file():
                _run_quiet(["explorer", f"/select,{str(target)}"])
            else:
                _run_quiet(["explorer", str(folder)])
        else:  # pragma: no cover - linux/bsd only
            _run_quiet(["xdg-open", str(folder)])
    except (OSError, subprocess.SubprocessError) as exc:
        raise FileActionError(f"Could not open the folder: {exc}") from exc


def existing_files(paths: Iterable[Optional[str | Path]]) -> list[Path]:
    """Filter candidates down to files that really exist right now."""
    out: list[Path] = []
    for candidate in paths:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            out.append(path)
    return out
