"""Turn yt-dlp's raw ``formats`` list into a short, honest set of choices.

Design goals:

* Only surface resolutions/containers that the source actually provides.
* Distinguish exact file sizes from estimates (``size_is_estimate``).
* Say up front when an option needs FFmpeg (separate-stream merge or audio
  conversion) so the UI can disable it with an explanation instead of failing
  halfway through.
* Never promise a quality that is unavailable: options are built from the
  metadata, not from a fixed list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional

from downloader.progress import format_bytes

Kind = Literal["video", "audio"]


@dataclass(frozen=True)
class QualityOption:
    """One user-facing choice on the Home page."""

    key: str
    label: str
    kind: Kind
    format_selector: str
    height: Optional[int] = None
    requires_merge: bool = False
    requires_ffmpeg: bool = False
    size_bytes: Optional[int] = None
    size_is_estimate: bool = False
    audio_ext: Optional[str] = None      # final container for audio downloads
    convert_audio: bool = False          # run FFmpegExtractAudio (e.g. mp3)
    container: Optional[str] = None      # requested merge container for video
    note: Optional[str] = None

    @property
    def size_label(self) -> str:
        if self.size_bytes is None:
            return ""
        suffix = " (est.)" if self.size_is_estimate else ""
        return f"{format_bytes(self.size_bytes)}{suffix}"


# --------------------------------------------------------------------------- #
# Safe accessors for untrusted metadata
# --------------------------------------------------------------------------- #


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return int(number) if number is not None and number >= 0 else None


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _has_video(fmt: dict) -> bool:
    vcodec = _text(fmt.get("vcodec"))
    if vcodec is not None:
        return vcodec.lower() != "none"
    # vcodec missing: infer from the stream description.
    return _int(fmt.get("height")) is not None or _num(fmt.get("vbr")) is not None


def _has_audio(fmt: dict) -> bool:
    acodec = _text(fmt.get("acodec"))
    if acodec is not None:
        return acodec.lower() != "none"
    # acodec missing: infer from the stream description.
    vcodec = _text(fmt.get("vcodec"))
    if vcodec is not None and vcodec.lower() == "none":
        return _num(fmt.get("abr")) is not None or _num(fmt.get("tbr")) is not None
    return _num(fmt.get("abr")) is not None


def _split_formats(raw_formats: Any) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (combined, video_only, audio_only) with unknown entries dropped."""
    combined: list[dict] = []
    video_only: list[dict] = []
    audio_only: list[dict] = []
    if not isinstance(raw_formats, Iterable) or isinstance(raw_formats, (str, bytes)):
        return combined, video_only, audio_only

    for fmt in raw_formats:
        if not isinstance(fmt, dict):
            continue
        video = _has_video(fmt)
        audio = _has_audio(fmt)
        if video and audio:
            combined.append(fmt)
        elif video:
            video_only.append(fmt)
        elif audio:
            audio_only.append(fmt)
    return combined, video_only, audio_only


def _exact_size(fmt: dict) -> Optional[int]:
    """File size the extractor reports as exact."""
    size = _int(fmt.get("filesize"))
    if size is not None:
        return size
    # http_formats sometimes only carry filesize on the protocol variant.
    for http_fmt in fmt.get("http_fmts") or ():
        if isinstance(http_fmt, dict):
            size = _int(http_fmt.get("filesize"))
            if size is not None:
                return size
    return None


def _approx_size(fmt: dict) -> Optional[int]:
    return _int(fmt.get("filesize_approx"))


def _duration_of(fmt: dict, default_duration: Optional[int]) -> Optional[int]:
    return _int(fmt.get("duration")) or default_duration


def _size_from_bitrate(
    fmt: dict, duration: Optional[int], *, audio: bool = False
) -> Optional[int]:
    """Estimate bytes from bitrate × duration. Never exact."""
    if not duration:
        return None
    if audio:
        kbps = _num(fmt.get("abr")) or _num(fmt.get("tbr"))
    else:
        kbps = _num(fmt.get("tbr")) or _num(fmt.get("vbr"))
    if not kbps:
        return None
    return int(kbps * 1000 * duration / 8)


def _best(fmts: list[dict]) -> Optional[dict]:
    """Pick the 'best' format by resolution/bitrate, None-safe."""

    def sort_key(fmt: dict) -> tuple[float, float, float]:
        return (
            float(_int(fmt.get("height")) or 0),
            float(_num(fmt.get("tbr")) or 0),
            float(_num(fmt.get("fps")) or 0),
        )

    return max(fmts, key=sort_key) if fmts else None


def _best_audio(fmts: list[dict]) -> Optional[dict]:
    def sort_key(fmt: dict) -> tuple[float, float]:
        return (float(_num(fmt.get("abr")) or 0), float(_num(fmt.get("tbr")) or 0))

    return max(fmts, key=sort_key) if fmts else None


def _combined_size(fmt: dict, duration: Optional[int]) -> tuple[Optional[int], bool]:
    exact = _exact_size(fmt)
    if exact is not None:
        return exact, False
    approx = _approx_size(fmt)
    if approx is not None:
        return approx, True
    estimated = _size_from_bitrate(fmt, duration)
    if estimated is not None:
        return estimated, True
    return None, False


def _merged_size(
    video: dict, audio: Optional[dict], duration: Optional[int]
) -> tuple[Optional[int], bool]:
    v_exact = _exact_size(video)
    a_exact = _exact_size(audio) if audio else None
    if v_exact is not None and (a_exact is not None or audio is None):
        return v_exact + (a_exact or 0), False

    v_approx = _approx_size(video)
    a_approx = _approx_size(audio) if audio else None
    if v_approx is not None:
        return v_approx + (a_approx or 0), True

    v_est = _size_from_bitrate(video, _duration_of(video, duration))
    a_est = (
        _size_from_bitrate(audio, _duration_of(audio, duration), audio=True) if audio else 0
    )
    if v_est is not None:
        return v_est + (a_est or 0), True
    return None, False


# --------------------------------------------------------------------------- #
# Option builders
# --------------------------------------------------------------------------- #


def build_video_options(
    raw_formats: Any,
    *,
    duration: Optional[int] = None,
    container: str = "mp4",
) -> list[QualityOption]:
    """Build user-facing quality choices for a video download.

    Returns ``[Best available, <height>p, …]`` for the heights the source
    really offers, best first. Always returns at least one option so the UI
    has something valid to show; if the extractor reported no usable formats
    the single option falls back to yt-dlp's ``best`` selector.
    """
    combined, video_only, audio_only = _split_formats(raw_formats)

    if not combined and not video_only:
        return [
            QualityOption(
                key="best",
                label="Best available",
                kind="video",
                format_selector="bestvideo+bestaudio/best",
                requires_merge=True,
                requires_ffmpeg=True,
                container=container,
                note="Format list unavailable — yt-dlp will pick the best format. "
                "FFmpeg is used when separate streams must be merged.",
            )
        ]

    best_audio = _best_audio(
        [f for f in (*audio_only, *combined) if _has_audio(f)]
    )

    options: list[QualityOption] = []

    # ---- "Best available" -------------------------------------------------
    if video_only:
        selector = "bestvideo+bestaudio/best"
        needs_ffmpeg = True
        needs_merge = True
        note = "Highest available quality; video and audio are merged with FFmpeg."
        v_size_src = _best(video_only)
        size, estimate = _merged_size(v_size_src, best_audio, duration) if v_size_src else (None, False)
    else:
        selector = "best/bestvideo+bestaudio"
        needs_ffmpeg = False
        needs_merge = False
        note = "Single combined stream — no processing needed."
        best_combined = _best(combined)
        size, estimate = _combined_size(best_combined, duration) if best_combined else (None, False)

    options.append(
        QualityOption(
            key="best",
            label="Best available",
            kind="video",
            format_selector=selector,
            requires_merge=needs_merge,
            requires_ffmpeg=needs_ffmpeg,
            size_bytes=size,
            size_is_estimate=estimate,
            container=container,
            note=note,
        )
    )

    # ---- One entry per real height ---------------------------------------
    heights = sorted(
        {
            h
            for fmt in (*video_only, *combined)
            if (h := _int(fmt.get("height"))) is not None and h > 0
        },
        reverse=True,
    )

    for height in heights:
        v_only = _best([f for f in video_only if _int(f.get("height")) == height])
        comb = _best([f for f in combined if _int(f.get("height")) == height])
        key = f"{height}p"

        if v_only is not None:
            # Merge the dedicated video stream with the best audio stream,
            # falling back to a pre-merged format of the same height.
            selector = f"bestvideo[height={height}]+bestaudio/best[height={height}]"
            requires_merge = True
            requires_ffmpeg = True
            note = "Video and audio streams are merged with FFmpeg."
            size, estimate = _merged_size(v_only, best_audio, duration)
        else:
            selector = f"best[height={height}]"
            requires_merge = False
            requires_ffmpeg = False
            note = "Combined stream — no processing needed."
            size, estimate = _combined_size(comb, duration) if comb else (None, False)

        options.append(
            QualityOption(
                key=key,
                label=f"{height}p",
                kind="video",
                format_selector=selector,
                height=height,
                requires_merge=requires_merge,
                requires_ffmpeg=requires_ffmpeg,
                size_bytes=size,
                size_is_estimate=estimate,
                container=container,
                note=note,
            )
        )

    return options


_AUDIO_FORMAT_NOTE = {
    "m4a": "AAC audio, widely compatible.",
    "aac": "AAC audio.",
    "opus": "Opus audio (compact, great quality).",
    "webm": "WebM/Opus audio.",
    "ogg": "Ogg/Opus audio.",
    "mp3": "MP3 audio.",
    "flac": "Lossless FLAC audio.",
    "wav": "Lossless WAV audio.",
}


def build_audio_options(
    raw_formats: Any,
    *,
    duration: Optional[int] = None,
    allow_mp3: bool = True,
) -> list[QualityOption]:
    """Build audio choices: MP3 conversion + source-provided audio formats."""
    combined, _video_only, audio_only = _split_formats(raw_formats)
    options: list[QualityOption] = []

    if allow_mp3:
        best_source = _best_audio(audio_only) or _best_audio(combined)
        size, estimate = (None, False)
        if best_source is not None:
            exact = _exact_size(best_source)
            if exact is not None:
                size, estimate = exact, False
            else:
                est = _size_from_bitrate(
                    best_source, _duration_of(best_source, duration), audio=True
                )
                size, estimate = (est, True) if est else (None, False)
        options.append(
            QualityOption(
                key="audio:mp3",
                label="MP3 (convert)",
                kind="audio",
                format_selector="bestaudio/best",
                audio_ext="mp3",
                convert_audio=True,
                requires_ffmpeg=True,
                size_bytes=size,
                size_is_estimate=estimate,
                note="Converted to MP3 with FFmpeg (re-encode, 192 kbps).",
            )
        )

    # Source-provided audio-only streams, grouped by container.
    by_ext: dict[str, dict] = {}
    for fmt in audio_only:
        ext = (_text(fmt.get("ext")) or "").lower()
        if not ext:
            continue
        current = by_ext.get(ext)
        if current is None or (_num(fmt.get("abr")) or 0) > (_num(current.get("abr")) or 0):
            by_ext[ext] = fmt

    if by_ext:
        ranked = sorted(
            by_ext.items(),
            key=lambda kv: (_num(kv[1].get("abr")) or 0, _num(kv[1].get("tbr")) or 0),
            reverse=True,
        )
        for ext, fmt in ranked:
            abr = _int(fmt.get("abr")) or _int(fmt.get("tbr"))
            exact = _exact_size(fmt)
            if exact is not None:
                size, estimate = exact, False
            else:
                est = _size_from_bitrate(fmt, _duration_of(fmt, duration), audio=True)
                size, estimate = (est, True) if est else (None, False)
            label = ext.upper()
            if abr:
                label = f"{label} · {abr} kbps"
            options.append(
                QualityOption(
                    key=f"audio:{ext}",
                    label=label,
                    kind="audio",
                    format_selector=f"bestaudio[ext={ext}]/bestaudio",
                    audio_ext=ext,
                    requires_ffmpeg=False,
                    size_bytes=size,
                    size_is_estimate=estimate,
                    note=_AUDIO_FORMAT_NOTE.get(ext, "Downloaded as provided by the source."),
                )
            )
    else:
        # No dedicated audio streams: the audio track is inside the combined
        # stream. Download it as-is, without re-encoding.
        best_combined = _best_audio(combined)
        size, estimate = (None, False)
        if best_combined is not None:
            exact = _exact_size(best_combined)
            if exact is not None:
                size, estimate = exact, False
            else:
                est = _size_from_bitrate(
                    best_combined, _duration_of(best_combined, duration), audio=True
                )
                size, estimate = (est, True) if est else (None, False)
        ext = _text(best_combined.get("ext")) if best_combined else None
        options.append(
            QualityOption(
                key="audio:best",
                label="Best audio" + (f" · {ext.upper()}" if ext else ""),
                kind="audio",
                format_selector="bestaudio/best",
                audio_ext=ext,
                requires_ffmpeg=False,
                size_bytes=size,
                size_is_estimate=estimate,
                note="Original audio track from the source (no conversion).",
            )
        )

    return options


# --------------------------------------------------------------------------- #
# Selection helpers
# --------------------------------------------------------------------------- #


def find_option(options: list[QualityOption], key: Optional[str]) -> Optional[QualityOption]:
    if not key:
        return None
    for option in options:
        if option.key == key:
            return option
    return None


def pick_option(
    options: list[QualityOption], preferred_key: Optional[str]
) -> tuple[Optional[QualityOption], bool]:
    """Return ``(option, matched)``; falls back to the first (best) option."""
    if not options:
        return None, False
    found = find_option(options, preferred_key)
    if found is not None:
        return found, True
    return options[0], False


def option_detail(option: Optional[QualityOption]) -> str:
    """One-line explanation shown under the quality dropdown."""
    if option is None:
        return ""
    parts: list[str] = []
    if option.size_label:
        parts.append(option.size_label)
    elif option.kind == "video" and option.height:
        parts.append("Size unknown until download starts")
    if option.container and option.requires_merge:
        parts.append(f"→ {option.container.upper()}")
    if option.note:
        parts.append(option.note)
    return "  ·  ".join(parts)
