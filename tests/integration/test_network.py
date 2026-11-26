"""Opt-in integration tests — these hit the real network.

They are excluded by default via ``pytest.ini`` (``-m "not integration"``).
Run them explicitly:

    .venv/bin/python -m pytest -m integration tests/integration -v

Requirements: a working internet connection and a YouTube-reachable network.
They are deliberately few and small (a 19-second public video, audio only) so
they stay quick and gentle on the source.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from downloader.engine import DownloadEngine, DownloadRequest
from downloader.metadata import fetch_media_info

pytestmark = pytest.mark.integration

# "Me at the zoo" — the first YouTube video, public since 2005.
VIDEO_ID = "jNQXAC9IVRw"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def test_fetch_media_info_end_to_end():
    info = fetch_media_info(VIDEO_URL)

    assert info.video_id == VIDEO_ID
    assert info.title and "zoo" in info.title.lower()
    assert info.uploader
    assert info.duration and info.duration > 0
    assert info.formats, "expected at least one downloadable format"
    assert info.webpage_url and VIDEO_ID in info.webpage_url


def test_real_audio_download(tmp_path: Path):
    request = DownloadRequest(
        url=VIDEO_URL,
        title="Integration test video",
        destination=tmp_path,
        mode="audio",
        format_selector="bestaudio[ext=m4a]/bestaudio/best",
        quality_label="Best audio",
        format_label="Source audio",
        video_id=VIDEO_ID,
        audio_ext=None,
        convert_audio=False,   # no FFmpeg needed: stream copy only
        requires_ffmpeg=False,
    )

    engine = DownloadEngine()
    cancel_event = threading.Event()
    outcome: dict[str, object] = {}

    def worker() -> None:
        outcome["result"] = engine.run(
            request, cancel_event, on_progress=lambda _info: None
        )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=180)
    if thread.is_alive():
        cancel_event.set()
        thread.join(timeout=30)
        pytest.fail("real download did not finish within the timeout")

    result = outcome.get("result")
    assert result is not None, "engine produced no result"
    assert result.ok, f"download failed: {result.error}"

    produced = Path(result.file_path)
    assert produced.is_file(), f"engine reported {produced}, which does not exist"
    assert produced.stat().st_size > 0
    assert produced.parent == tmp_path
