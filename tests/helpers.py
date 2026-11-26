"""Pure helpers shared by tests (fixtures live in tests/conftest.py).

Imported as ``tests.helpers`` — the project root is placed on ``sys.path`` by
the root conftest before any test module is collected.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from app import config
from app.state import AppSettings, EventBus
from downloader.engine import DownloadRequest, EngineResult
from downloader.progress import DownloadStatus, ProgressInfo, TERMINAL_STATUSES
from services.download_service import DownloadService
from services.media_service import FFmpegInfo


def make_request(
    tmp_path: Path,
    url: str = "https://example.com/watch?v=1",
    **overrides,
) -> DownloadRequest:
    defaults = dict(
        url=url,
        title="Sample video",
        destination=Path(tmp_path),
        mode="video",
        format_selector="bestvideo+bestaudio/best",
        quality_label="Best available",
        format_label="Merged → MP4",
        video_id="vid0001",
        container="mp4",
    )
    defaults.update(overrides)
    return DownloadRequest(**defaults)


def make_service(
    tmp_path: Path,
    *,
    run: Optional[Callable] = None,
    history=None,
    ffmpeg_available: bool = True,
    max_concurrent: int = 1,
    bus: Optional[EventBus] = None,
):
    """Build a DownloadService with a stubbed engine (no yt-dlp, no network)."""
    event_bus = bus or EventBus()
    settings = AppSettings.from_dict(
        {
            **config.DEFAULT_SETTINGS,
            "download_dir": str(tmp_path),
            "max_concurrent": str(max_concurrent),
        }
    )
    ffmpeg = FFmpegInfo(
        available=ffmpeg_available,
        path="/opt/homebrew/bin/ffmpeg" if ffmpeg_available else None,
        version="8.0-test" if ffmpeg_available else None,
        source="path" if ffmpeg_available else "none",
    )
    service = DownloadService(
        settings_provider=lambda: settings,
        event_bus=event_bus,
        ffmpeg_provider=lambda: ffmpeg,
        history=history,
    )
    if run is not None:
        service._engine.run = run  # type: ignore[method-assign]
    return service, event_bus


def wait_terminal(service: DownloadService, task_id: str, timeout: float = 5.0):
    """Poll until the task reaches a terminal status (fails the test on timeout)."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        snapshot = service.get(task_id)
        if snapshot is not None:
            last = snapshot
            if snapshot.status in TERMINAL_STATUSES:
                return snapshot
        time.sleep(0.02)
    raise AssertionError(
        f"task {task_id} never reached a terminal status (last: {last and last.status})"
    )


def wait_status(
    service: DownloadService,
    task_id: str,
    status: DownloadStatus,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.get(task_id)
        if snapshot is not None and snapshot.status is status:
            return
        time.sleep(0.02)
    last = service.get(task_id)
    raise AssertionError(
        f"task {task_id} never reached {status.value} (last: {last and last.status})"
    )


def success_engine(file_dir: Path):
    """Engine stub that writes a small output file and succeeds."""

    def run(request, cancel_event, on_progress, on_stage=None):
        on_progress(
            ProgressInfo(
                status=DownloadStatus.DOWNLOADING,
                percent=50.0,
                downloaded_bytes=50,
                total_bytes=100,
                speed=10.0,
                eta=5,
            )
        )
        if cancel_event.is_set():
            return EngineResult(ok=False, cancelled=True, error="Cancelled by user")
        output = Path(request.destination) / f"{request.video_id or 'out'}-out.mp4"
        output.write_bytes(b"\x00" * 128)
        return EngineResult(ok=True, file_path=output)

    return run


def failing_engine(message: str = "Simulated engine failure"):
    def run(request, cancel_event, on_progress, on_stage=None):
        on_progress(
            ProgressInfo(
                status=DownloadStatus.DOWNLOADING,
                percent=33.0,
                downloaded_bytes=1,
                total_bytes=3,
            )
        )
        if cancel_event.is_set():
            return EngineResult(ok=False, cancelled=True, error="Cancelled by user")
        return EngineResult(ok=False, error=message, error_kind="download")

    return run


def blocking_engine(started: threading.Event, release: threading.Event):
    """Engine stub that runs until its cancel event is set (or released)."""

    def run(request, cancel_event, on_progress, on_stage=None):
        started.set()
        while not release.is_set():
            if cancel_event.is_set():
                return EngineResult(
                    ok=False, cancelled=True, error="Cancelled by user"
                )
            release.wait(0.02)
        # Released by the test without cancellation: finish as a plain failure.
        return EngineResult(ok=False, error="released without cancel")

    return run
