"""Metadata retrieval (no downloading) and thumbnail loading.

``fetch_media_info`` calls yt-dlp with ``download=False``/``skip_download``
so no media bytes are transferred, and every field is read defensively —
extractors may omit title/uploader/duration/thumbnail/formats at any time.
"""

from __future__ import annotations

import io
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yt_dlp
from PIL import Image

from app import config
from downloader.validators import redact_url

logger = logging.getLogger(__name__)

# Cap on thumbnail downloads (metadata only — never media).
_MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024
_THUMBNAIL_TIMEOUT = 12.0
_USER_AGENT = "Mozilla/5.0 (compatible; YT-Downloader/1.0)"


class MetadataError(RuntimeError):
    """Metadata could not be retrieved; ``str(exc)`` is user-displayable."""


@dataclass(frozen=True)
class MediaInfo:
    url: str
    video_id: Optional[str]
    title: str
    uploader: Optional[str]
    duration: Optional[int]
    thumbnail_url: Optional[str]
    formats: list[dict] = field(default_factory=list)
    webpage_url: Optional[str] = None
    extractor: Optional[str] = None
    is_live: bool = False
    original_url: Optional[str] = None


def default_metadata_opts(socket_timeout: float = 20.0) -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "retries": 1,
        "socket_timeout": socket_timeout,
        "cachedir": str(config.app_data_dir() / "cache" / "ytdlp"),
    }


def fetch_media_info(url: str, *, socket_timeout: float = 20.0) -> MediaInfo:
    """Retrieve media information for a single video URL.

    Raises :class:`MetadataError` with a friendly message on any failure.
    Never downloads media.
    """
    opts = default_metadata_opts(socket_timeout)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=True)
    except yt_dlp.utils.DownloadCancelled:
        raise MetadataError("Fetching information was cancelled.")
    except yt_dlp.utils.DownloadError as exc:
        raise MetadataError(friendly_error(exc)) from exc
    except yt_dlp.utils.ExtractorError as exc:
        raise MetadataError(friendly_error(exc)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise MetadataError("The request timed out. Check your connection and try again.") from exc
    except urllib.error.HTTPError as exc:
        raise MetadataError(friendly_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise MetadataError(friendly_error(exc)) from exc
    except OSError as exc:
        raise MetadataError(friendly_error(exc)) from exc

    if not isinstance(info, dict):
        raise MetadataError("No information was returned for that URL.")

    if info.get("_type") in {"playlist", "multi_video"} or (
        info.get("entries") is not None and info.get("_type") != "video"
    ):
        raise MetadataError(
            "Playlists and channels aren't supported yet — paste a link to a single video."
        )

    title = _clean_str(info.get("title")) or "Untitled video"
    uploader = (
        _clean_str(info.get("uploader"))
        or _clean_str(info.get("channel"))
        or _clean_str(info.get("creator"))
        or _clean_str(info.get("album_artist"))
    )
    duration = _clean_int(info.get("duration"))
    thumbnail = _clean_str(info.get("thumbnail"))
    if thumbnail is None:
        thumbnails = info.get("thumbnails")
        if isinstance(thumbnails, list) and thumbnails:
            for entry in reversed(thumbnails):
                if isinstance(entry, dict) and _clean_str(entry.get("url")):
                    thumbnail = _clean_str(entry.get("url"))
                    break

    raw_formats = info.get("formats")
    formats = [f for f in raw_formats if isinstance(f, dict)] if isinstance(raw_formats, list) else []

    if duration is not None and duration <= 0:
        duration = None

    return MediaInfo(
        url=url,
        video_id=_clean_str(info.get("id")),
        title=title,
        uploader=uploader,
        duration=duration,
        thumbnail_url=thumbnail,
        formats=formats,
        webpage_url=_clean_str(info.get("webpage_url")) or url,
        extractor=_clean_str(info.get("extractor_key")) or _clean_str(info.get("extractor")),
        is_live=bool(info.get("is_live")),
        original_url=_clean_str(info.get("original_url")) or url,
    )


def _clean_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _clean_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Friendly error messages
# --------------------------------------------------------------------------- #

_ERROR_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("sign in to confirm", "not a bot", "confirm you're not a bot"),
        "The source asked for bot verification. Wait a while and try again, "
        "or try a different network.",
    ),
    (
        ("age-restricted", "age restricted", "confirm your age", "inappropriate for everyone"),
        "This video is age-restricted and can't be downloaded by this app.",
    ),
    (
        ("private video", "this video is private", "video is private"),
        "This video is private and isn't available for download.",
    ),
    (
        ("members-only", "members only", "join this channel", "paid content"),
        "This video is available to channel members only and can't be downloaded.",
    ),
    (
        ("video unavailable", "this video is unavailable", "has been removed", "not available anymore"),
        "The video is unavailable — it may have been deleted or made private.",
    ),
    (
        ("copyright", "has been taken down", "removed by the"),
        "The video has been taken down and can't be downloaded.",
    ),
    (
        ("not available in your country", "geo", "restricted from uploading"),
        "The video is geo-restricted and can't be accessed from your location.",
    ),
    (
        ("live event will begin", "premieres", "is live", "live stream"),
        "Live streams and premieres can't be downloaded before/during broadcast.",
    ),
    (
        ("unsupported url", "no suitable extractor"),
        "That URL isn't supported. Paste a link to a supported video page.",
    ),
    (
        ("requested format is not available", "requested format not available"),
        "The selected quality isn't available for this video. Pick another option.",
    ),
    (
        ("ffmpeg is not installed", "ffmpeg not found", "ffprobe not found"),
        "FFmpeg is required for this operation. Install it with: brew install ffmpeg",
    ),
    (
        ("http error 429", "too many requests"),
        "Too many requests (429). Wait a moment and try again.",
    ),
    (
        ("http error 403", "access denied"),
        "The source refused access (403). The content may be restricted.",
    ),
    (
        ("http error 404", "not found"),
        "The page wasn't found (404). Check the URL.",
    ),
    (
        ("http error 451", "unavailable for legal reasons"),
        "The content is unavailable for legal reasons (451).",
    ),
    (
        (
            "timed out", "timeout", "connection refused", "connection reset",
            "network is unreachable", "temporary failure in name resolution",
            "getaddrinfo failed", "unable to download webpage", "no route to host",
            "name resolution",
        ),
        "Network error — check your internet connection and try again.",
    ),
)


def friendly_error(exc: BaseException | str) -> str:
    """Map a low-level exception/message to a short, human explanation."""
    raw = str(exc).strip()
    lowered = raw.lower()
    for needles, message in _ERROR_RULES:
        if any(needle in lowered for needle in needles):
            return message
    # Unknown errors: keep the first line, trimmed, so context survives
    # without dumping a traceback into the UI.
    first_line = raw.splitlines()[0].strip() if raw else "Unknown error"
    if len(first_line) > 220:
        first_line = first_line[:217] + "…"
    return first_line or "Unknown error"


# --------------------------------------------------------------------------- #
# Thumbnails
# --------------------------------------------------------------------------- #


def fetch_thumbnail(url: str) -> Optional[Image.Image]:
    """Download and decode a thumbnail image, or return ``None`` on failure.

    Validates the scheme, caps the size and decodes through Pillow so a
    malformed/hostile payload cannot escape the image decoder.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"}:
        return None

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_THUMBNAIL_TIMEOUT) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > _MAX_THUMBNAIL_BYTES:
                logger.info("Thumbnail too large (%s bytes), skipping", length)
                return None
            data = response.read(_MAX_THUMBNAIL_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.info("Thumbnail fetch failed for %s: %s", redact_url(url), exc)
        return None

    if len(data) > _MAX_THUMBNAIL_BYTES:
        logger.info("Thumbnail exceeded size cap, skipping")
        return None

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image.convert("RGB")
    except Exception:  # Pillow raises many unrelated exception types
        logger.info("Thumbnail was not a valid image")
        return None


def thumbnail_cache_path(url: str) -> Path:
    """Deterministic, sanitised cache location for a thumbnail URL."""
    import hashlib

    from services.file_service import sanitize_filename

    digest = hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()[:24]
    basename = url.split("?")[0].rsplit("/", 1)[-1] or "thumb"
    return config.thumbnail_cache_dir() / f"{digest}_{sanitize_filename(basename, 60)}"


def load_thumbnail(url: str) -> Optional[Image.Image]:
    """Fetch a thumbnail with a small on-disk cache (history rows reuse it)."""
    cache_file = thumbnail_cache_path(url)
    if cache_file.is_file() and cache_file.stat().st_size <= _MAX_THUMBNAIL_BYTES:
        try:
            image = Image.open(cache_file)
            image.load()
            return image.convert("RGB")
        except Exception:
            try:
                cache_file.unlink()
            except OSError:
                pass

    image = fetch_thumbnail(url)
    if image is not None:
        try:
            image.save(cache_file, format="JPEG", quality=85)
        except OSError:
            logger.debug("Could not write thumbnail cache file", exc_info=True)
    return image
