"""Environment services: FFmpeg detection, yt-dlp version, update checks."""

from __future__ import annotations

import json
import logging
import platform
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:  # ``packaging`` ships with customtkinter
    from packaging.version import Version

    def _version_tuple(value: str) -> tuple:
        try:
            return (Version(value),)
        except Exception:
            return (_naive_version(value),)

except Exception:  # pragma: no cover - packaging always present in practice

    def _version_tuple(value: str) -> tuple:
        return (_naive_version(value),)


def _naive_version(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(p) for p in parts) or (0,)


@dataclass(frozen=True)
class FFmpegInfo:
    available: bool
    path: Optional[str] = None
    version: Optional[str] = None
    source: str = "none"  # "configured" | "path" | "none"
    error: Optional[str] = None

    @property
    def label(self) -> str:
        if not self.available:
            return self.error or "FFmpeg not found"
        version = f" ({self.version})" if self.version else ""
        return f"FFmpeg found — {self.path}{version}"


def _run_version(executable: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(version, error)`` for an ffmpeg executable."""
    try:
        proc = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"Could not run {executable}: {exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return None, f"{executable} exited with code {proc.returncode}"
    match = re.search(r"ffmpeg version (\S+)", output)
    return (match.group(1) if match else "unknown version"), None


def detect_ffmpeg(configured_path: str = "") -> FFmpegInfo:
    """Locate FFmpeg: user-configured path first, then ``PATH``."""
    configured = (configured_path or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            for name in ("ffmpeg", "ffmpeg.exe"):
                inside = candidate / name
                if inside.is_file():
                    candidate = inside
                    break
        if candidate.is_file():
            version, error = _run_version(str(candidate))
            if version:
                return FFmpegInfo(True, str(candidate), version, "configured")
            logger.warning("Configured FFmpeg is not usable: %s", error)
        else:
            logger.warning("Configured FFmpeg path does not exist: %s", configured)

    discovered = shutil.which("ffmpeg")
    if discovered:
        version, error = _run_version(discovered)
        if version:
            return FFmpegInfo(True, discovered, version, "path")
        logger.warning("ffmpeg on PATH is not usable: %s", error)

    return FFmpegInfo(
        False,
        None,
        None,
        "none",
        "FFmpeg is not installed (or not on PATH).",
    )


def ffmpeg_install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "Install it with Homebrew:  brew install ffmpeg"
    if system == "Windows":  # pragma: no cover - windows only
        return "Install it with winget:  winget install Gyan.FFmpeg  (then restart the app)"
    return "Install it with your package manager, e.g.:  sudo apt install ffmpeg"


def yt_dlp_version() -> str:
    try:
        import yt_dlp

        return getattr(yt_dlp.version, "__version__", "unknown")
    except Exception:  # pragma: no cover - yt_dlp always importable here
        return "unavailable"


def check_for_ytdlp_update(timeout: float = 6.0) -> Optional[tuple[str, str]]:
    """Compare the installed yt-dlp with the latest release on PyPI.

    Returns ``(installed, latest)`` only when a newer release exists,
    ``None`` when up to date, and raises :class:`OSError`-family errors the
    caller should treat as "could not check" (never an automatic install).
    """
    installed = yt_dlp_version()
    url = "https://pypi.org/pypi/yt-dlp/json"
    request = urllib.request.Request(url, headers={"User-Agent": "YT-Downloader/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latest = str((payload.get("info") or {}).get("version") or "").strip()
    if not latest:
        return None
    if _version_tuple(latest) > _version_tuple(installed):
        return installed, latest
    return None
