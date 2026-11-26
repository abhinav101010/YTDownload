"""yt-dlp download engine.

Wraps the yt-dlp Python API behind a small, testable interface:

* :func:`build_ydl_opts` builds the option dict from a
  :class:`DownloadRequest` (pure function — unit-testable without network).
* :class:`DownloadEngine.run` executes the download in the calling thread
  (the download service calls it from worker threads), reporting progress via
  callbacks and honouring a :class:`threading.Event` for cancellation.

Cancellation is implemented by raising ``yt_dlp.utils.DownloadCancelled``
from the progress/postprocessor hooks — yt-dlp aborts the download and the
exception propagates out of ``extract_info`` (verified against yt-dlp
2026.08.19: ``__download_wrapper`` re-raises when ``break_per_url`` is off,
which is the default).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError

from app import config
from downloader.progress import (
    DownloadStatus,
    ProgressInfo,
    parse_postprocessor,
    parse_progress,
)
from downloader.validators import redact_url
from services.file_service import cleanup_partial_files

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[ProgressInfo], None]


class EngineErrorKind:
    DESTINATION = "destination"
    FFMPEG = "ffmpeg"
    NETWORK = "network"
    DOWNLOAD = "download"
    PROCESSING = "processing"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


@dataclass(frozen=True)
class DownloadRequest:
    """Everything the engine needs for one download (built by the UI)."""

    url: str
    title: str
    destination: Path
    mode: str  # "video" | "audio"
    format_selector: str
    quality_label: str
    format_label: str
    video_id: Optional[str] = None
    container: Optional[str] = None      # merge container for video mode
    audio_ext: Optional[str] = None      # final ext for audio mode
    convert_audio: bool = False          # FFmpegExtractAudio (e.g. mp3)
    requires_ffmpeg: bool = False
    ffmpeg_path: Optional[str] = None    # configured ffmpeg binary/dir
    thumbnail_url: Optional[str] = None
    estimated_size: Optional[int] = None  # bytes; may be an estimate


@dataclass
class EngineResult:
    ok: bool
    file_path: Optional[Path] = None
    cancelled: bool = False
    error: Optional[str] = None
    error_kind: Optional[str] = None


class _YDLLogger:
    """Forward yt-dlp diagnostics into the application log (no console)."""

    def debug(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        logger.info("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        logger.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        logger.error("yt-dlp: %s", msg)


# --------------------------------------------------------------------------- #
# Option building (pure)
# --------------------------------------------------------------------------- #


def build_ydl_opts(
    request: DownloadRequest,
    *,
    progress_hook: Optional[Callable[[dict], None]] = None,
    postprocessor_hook: Optional[Callable[[dict], None]] = None,
) -> dict[str, Any]:
    """Build the yt-dlp options dict for one request."""
    destination = Path(request.destination)
    # Truncated title + [id] keeps names readable and under filesystem limits;
    # the id marker also lets cleanup_partial_files() target only our files.
    outtmpl = str(destination / "%(title).200B [%(id)s].%(ext)s")

    postprocessors: list[dict[str, Any]] = []
    if request.mode == "audio" and request.convert_audio:
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "format": (request.audio_ext or "mp3").lower(),
                "preferredquality": "192",
                "when": "post_process",
            }
        )

    opts: dict[str, Any] = {
        "format": request.format_selector or "best",
        "outtmpl": {"default": outtmpl},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "ignoreerrors": False,          # raise so cancellation/errors propagate
        "retries": 2,
        "fragment_retries": 4,
        "socket_timeout": 20,
        "overwrites": False,            # never clobber an existing file
        "continuedl": True,             # resume .part files when possible
        "nopart": False,
        "retries_sleep": 2,
        "concurrent_fragment_downloads": 4,
        "logger": _YDLLogger(),
        "writethumbnail": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
        # Keep yt-dlp's extractor cache in the app data dir, not the user's
        # download folder.
        "cachedir": str(config.app_data_dir() / "cache" / "ytdlp"),
    }

    if progress_hook is not None:
        opts["progress_hooks"] = [progress_hook]
    if postprocessor_hook is not None:
        opts["postprocessor_hooks"] = [postprocessor_hook]
    if postprocessors:
        opts["postprocessors"] = postprocessors
    if request.mode == "video" and request.container:
        opts["merge_output_format"] = request.container.lower()
    if request.ffmpeg_path:
        opts["ffmpeg_location"] = request.ffmpeg_path
    return opts


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class DownloadEngine:
    """Runs one download at a time; called from worker threads."""

    def run(
        self,
        request: DownloadRequest,
        cancel_event: threading.Event,
        on_progress: ProgressCallback,
        on_stage: Optional[ProgressCallback] = None,
    ) -> EngineResult:
        """Execute the download. Never raises — returns an :class:`EngineResult`."""

        def progress_hook(data: dict) -> None:
            if cancel_event.is_set():
                raise DownloadCancelled("Cancelled by user")
            status = data.get("status") if isinstance(data, dict) else None
            if status == "downloading":
                on_progress(parse_progress(data))
            elif status == "finished":
                # One stream finished — merged downloads still need
                # post-processing. Never signal completion yet.
                if on_stage is not None:
                    on_stage(
                        ProgressInfo(
                            status=DownloadStatus.DOWNLOADING,
                            stage="Download finished — finalizing…",
                        )
                    )
            elif status == "error":
                logger.debug("yt-dlp reported a download error for a stream")

        def postprocessor_hook(data: dict) -> None:
            if cancel_event.is_set():
                raise DownloadCancelled("Cancelled by user")
            if not isinstance(data, dict):
                return
            if data.get("status") in {"started", "processing"} and on_stage is not None:
                on_stage(parse_postprocessor(data))

        opts = build_ydl_opts(
            request, progress_hook=progress_hook, postprocessor_hook=postprocessor_hook
        )

        logger.info(
            "Starting download: %s | selector=%s | dest=%s",
            redact_url(request.url),
            request.format_selector,
            request.destination,
        )

        info: Optional[dict] = None
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(request.url, download=True, process=True)
                file_path = self._resolve_output_path(ydl, info, request)
        except DownloadCancelled:
            self._cleanup_partials(request)
            logger.info("Download cancelled: %s", redact_url(request.url))
            return EngineResult(
                ok=False,
                cancelled=True,
                error="Cancelled by user",
                error_kind=EngineErrorKind.CANCELLED,
            )
        except DownloadError as exc:
            self._cleanup_partials(request)
            kind = self._classify_error(exc)
            logger.warning("Download failed (%s): %s", kind, redact_url(request.url))
            return EngineResult(
                ok=False, error=self._friendly(exc, kind), error_kind=kind
            )
        except ExtractorError as exc:
            self._cleanup_partials(request)
            logger.warning("Extraction failed: %s", redact_url(request.url))
            return EngineResult(
                ok=False,
                error=self._friendly(exc, EngineErrorKind.DOWNLOAD),
                error_kind=EngineErrorKind.DOWNLOAD,
            )
        except OSError as exc:
            # Disk full / permission errors while writing the file.
            self._cleanup_partials(request)
            logger.error("Filesystem error during download: %s", exc)
            return EngineResult(
                ok=False,
                error=f"A filesystem error occurred: {exc.strerror or exc}",
                error_kind=EngineErrorKind.DESTINATION,
            )
        except Exception as exc:  # pragma: no cover - defensive last resort
            self._cleanup_partials(request)
            logger.exception("Unexpected engine error")
            return EngineResult(
                ok=False,
                error=f"Unexpected error: {type(exc).__name__}",
                error_kind=EngineErrorKind.INTERNAL,
            )

        if cancel_event.is_set():
            # Cancel arrived after the last hook: stop before reporting success.
            self._cleanup_partials(request)
            return EngineResult(
                ok=False,
                cancelled=True,
                error="Cancelled by user",
                error_kind=EngineErrorKind.CANCELLED,
            )

        if file_path is None or not file_path.is_file():
            return EngineResult(
                ok=False,
                error=(
                    "The download finished but the output file could not be "
                    "located in the destination folder."
                ),
                error_kind=EngineErrorKind.DESTINATION,
            )

        logger.info("Download complete: %s", file_path.name)
        return EngineResult(ok=True, file_path=file_path)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _resolve_output_path(
        self, ydl: Any, info: Optional[dict], request: DownloadRequest
    ) -> Optional[Path]:
        """Locate the final file after download/post-processing."""
        if not isinstance(info, dict):
            return None
        candidates: list[Optional[str]] = []
        for entry in info.get("requested_downloads") or []:
            if isinstance(entry, dict):
                candidates.append(entry.get("filepath"))
                candidates.append(entry.get("_filename"))
        candidates.append(info.get("filepath"))
        candidates.append(info.get("_filename"))
        try:
            candidates.append(ydl.prepare_filename(info))
        except Exception:
            pass

        destination = Path(request.destination)
        # Post-processing can change the extension (e.g. -> mp3): probe both.
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate)
            for variant in (path, path.with_suffix(f".{request.audio_ext}") if request.audio_ext else None):
                if variant is not None and variant.is_file():
                    return variant

        # Last resort: search the destination for a file carrying the id
        # marker written by our output template.
        if request.video_id:
            marker = f"[{request.video_id}]"
            try:
                for candidate_file in sorted(destination.glob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
                    if candidate_file.is_file() and marker in candidate_file.name:
                        return candidate_file
            except OSError:
                pass
        return None

    def _cleanup_partials(self, request: DownloadRequest) -> None:
        try:
            removed = cleanup_partial_files(Path(request.destination), request.video_id)
            if removed:
                logger.debug("Removed %d partial file(s) after stop", len(removed))
        except OSError:
            logger.warning("Could not clean up partial files", exc_info=True)

    @staticmethod
    def _classify_error(exc: BaseException) -> str:
        text = str(exc).lower()
        if "ffmpeg" in text and ("not installed" in text or "not found" in text or "no such file" in text):
            return EngineErrorKind.FFMPEG
        if any(
            needle in text
            for needle in (
                "timed out", "timeout", "connection", "network", "urlopen",
                "getaddrinfo", "temporary failure", "unreachable", "name resolution",
            )
        ):
            return EngineErrorKind.NETWORK
        if "postprocessing" in text or "post process" in text:
            return EngineErrorKind.PROCESSING
        return EngineErrorKind.DOWNLOAD

    @staticmethod
    def _friendly(exc: BaseException, kind: str) -> str:
        from downloader.metadata import friendly_error

        message = friendly_error(exc)
        if kind == EngineErrorKind.FFMPEG and "brew install ffmpeg" not in message:
            return (
                "FFmpeg is required to finish this download (merge or conversion). "
                "Install it with: brew install ffmpeg"
            )
        if kind == EngineErrorKind.PROCESSING and not message:
            return "Media processing failed after the download finished."
        return message
