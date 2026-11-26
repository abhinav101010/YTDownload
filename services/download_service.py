"""Download orchestration: queue, workers, cancellation, retry, history.

The service owns :class:`_Task` objects and worker threads.  Workers never
touch the UI: every state change is published as an immutable
:class:`~downloader.progress.TaskSnapshot` on the
:class:`~app.state.EventBus`, which the Tk main loop drains with
``root.after(...)``.

Persistence: one history row per *run* (a retry creates a new row), written
from the worker thread through the thread-safe database layer.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.state import AppSettings, EVENT_DOWNLOAD, EventBus
from database.database import DatabaseError
from database.models import now_iso
from downloader.engine import DownloadEngine, DownloadRequest, EngineResult
from downloader.progress import (
    ACTIVE_STATUSES,
    DownloadStatus,
    InvalidTransition,
    ProgressInfo,
    TaskSnapshot,
    transition,
)
from downloader.validators import redact_url
from services.file_service import (
    DestinationError,
    InsufficientSpaceError,
    ensure_free_space,
    validate_destination,
)
from services.media_service import FFmpegInfo
from services.queue_service import TaskScheduler

logger = logging.getLogger(__name__)

_PROGRESS_POST_INTERVAL = 0.15  # seconds — keeps the UI smooth without flooding


@dataclass
class _Task:
    """Mutable internal task record (only its own worker writes status)."""

    id: str
    request: DownloadRequest
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: ProgressInfo = field(default_factory=ProgressInfo)
    error: Optional[str] = None
    file_path: Optional[str] = None
    history_id: Optional[int] = None
    created_at: str = field(default_factory=now_iso)
    finished_at: Optional[str] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _last_post: float = 0.0
    _posted_status: Optional[DownloadStatus] = None

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot(
            id=self.id,
            url=self.request.url,
            title=self.request.title,
            thumbnail_url=self.request.thumbnail_url,
            quality_label=self.request.quality_label,
            format_label=self.request.format_label,
            mode=self.request.mode,
            destination=str(self.request.destination),
            status=self.status,
            progress=self.progress,
            error=self.error,
            file_path=self.file_path,
            created_at=self.created_at,
            history_id=self.history_id,
        )


class DownloadService:
    """Bounded-concurrency download queue with honest status reporting."""

    def __init__(
        self,
        *,
        settings_provider: Callable[[], AppSettings],
        event_bus: EventBus,
        ffmpeg_provider: Callable[[], FFmpegInfo],
        engine: Optional[DownloadEngine] = None,
        history=None,  # optional HistoryRepository (None disables persistence)
    ) -> None:
        self._settings = settings_provider
        self._bus = event_bus
        self._ffmpeg = ffmpeg_provider
        self._engine = engine or DownloadEngine()
        self._history = history
        self._lock = threading.RLock()
        self._tasks: dict[str, _Task] = {}
        self._scheduler = TaskScheduler(max_concurrency=self._current_limit)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def _current_limit(self) -> int:
        try:
            return max(1, int(self._settings().max_concurrent))
        except Exception:
            return 1

    def snapshots(self) -> list[TaskSnapshot]:
        with self._lock:
            return [task.snapshot() for task in self._tasks.values()]

    def get(self, task_id: str) -> Optional[TaskSnapshot]:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.snapshot() if task else None

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for task in self._tasks.values() if task.status in ACTIVE_STATUSES)

    @property
    def has_active(self) -> bool:
        return self.active_count > 0 or self._scheduler.pending_count > 0

    @property
    def pending_count(self) -> int:
        return self._scheduler.pending_count

    # ------------------------------------------------------------------ #
    # Submitting / cancelling / retrying
    # ------------------------------------------------------------------ #

    def submit(self, request: DownloadRequest) -> tuple[bool, str]:
        """Queue a download. Returns ``(accepted, task_id_or_reason)``."""
        with self._lock:
            for task in self._tasks.values():
                if task.request.url == request.url and task.status in ACTIVE_STATUSES:
                    return False, "This URL is already queued or downloading."
            task_id = uuid.uuid4().hex[:12]
            task = _Task(id=task_id, request=request)
            self._tasks[task_id] = task

        self._scheduler.enqueue(task_id)
        self._post(task, force=True)
        self._pump()
        logger.info("Queued download %s for %s", task_id, redact_url(request.url))
        return True, task_id

    def cancel(self, task_id: str) -> tuple[bool, str]:
        """Request cancellation.

        Queued items are cancelled immediately (they never started).
        Running items keep their status until the worker actually stops —
        the UI is never told a download is cancelled before it really is.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False, "That download no longer exists."
            if task.status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED):
                return False, "That download has already finished."
            if task.status is DownloadStatus.CANCELLED:
                return False, "That download was already cancelled."

        if task.status is DownloadStatus.QUEUED and self._scheduler.remove_queued(task_id):
            self._finish(task, DownloadStatus.CANCELLED, error="Cancelled before it started.")
            return True, "Removed from the queue."

        task.cancel_event.set()
        logger.info("Cancellation requested for download %s", task_id)
        return True, "Stopping… the download will be cancelled as soon as possible."

    def retry(self, task_id: str) -> tuple[bool, str]:
        """Re-queue a failed or cancelled task (keeps the same list row)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False, "That download no longer exists."
            if task.status not in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                return False, "Only failed or cancelled downloads can be retried."
            task.status = transition(task.status, DownloadStatus.QUEUED)
            task.progress = ProgressInfo(status=DownloadStatus.QUEUED)
            task.error = None
            task.file_path = None
            task.history_id = None
            task.finished_at = None
            task.cancel_event.clear()

        self._scheduler.enqueue(task_id)
        self._post(task, force=True)
        self._pump()
        return True, "Queued again."

    def forget(self, task_id: str) -> bool:
        """Remove a finished task from the visible list (files/history stay)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in ACTIVE_STATUSES:
                return False
            del self._tasks[task_id]
        return True

    def clear_finished(self) -> int:
        """Remove every terminal task from the visible list. Returns count."""
        with self._lock:
            ids = [
                task_id
                for task_id, task in self._tasks.items()
                if task.status not in ACTIVE_STATUSES
            ]
            for task_id in ids:
                del self._tasks[task_id]
        return len(ids)

    def on_settings_changed(self) -> None:
        """Apply a new concurrency limit and start more work if possible."""
        self._pump()

    # ------------------------------------------------------------------ #
    # Worker machinery
    # ------------------------------------------------------------------ #

    def _pump(self) -> None:
        self._scheduler.pump(self._launch)

    def _launch(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status is not DownloadStatus.QUEUED:
                return
        thread = threading.Thread(
            target=self._run, args=(task,), name=f"ytdl-worker-{task.id}", daemon=True
        )
        self._scheduler.activate(task_id, thread)
        thread.start()

    def _run(self, task: _Task) -> None:
        try:
            task.status = transition(task.status, DownloadStatus.DOWNLOADING)
            task.progress = ProgressInfo(status=DownloadStatus.DOWNLOADING, stage="Starting…")
            self._post(task, force=True)

            self._create_history_row(task)

            preflight_error = self._preflight(task.request)
            if preflight_error:
                self._finish(task, DownloadStatus.FAILED, error=preflight_error)
                return

            result = self._engine.run(
                task.request,
                task.cancel_event,
                on_progress=lambda info: self._on_progress(task, info),
                on_stage=lambda info: self._on_stage(task, info),
            )
            self._apply_result(task, result)
        except Exception:
            logger.exception("Download worker %s crashed", task.id)
            self._finish(
                task,
                DownloadStatus.FAILED,
                error="An unexpected error occurred — see the log for details.",
            )
        finally:
            self._scheduler.release(task.id)
            self._pump()

    # ------------------------------------------------------------------ #
    # Engine callbacks (worker thread)
    # ------------------------------------------------------------------ #

    def _on_progress(self, task: _Task, info: ProgressInfo) -> None:
        task.progress = info
        self._post(task)

    def _on_stage(self, task: _Task, info: ProgressInfo) -> None:
        task.progress = info
        if info.status is DownloadStatus.PROCESSING and task.status is DownloadStatus.DOWNLOADING:
            try:
                task.status = transition(task.status, DownloadStatus.PROCESSING)
            except InvalidTransition:  # pragma: no cover - defensive
                logger.debug("Ignored invalid stage transition for %s", task.id)
        self._post(task, force=True)

    def _apply_result(self, task: _Task, result: EngineResult) -> None:
        if result.cancelled:
            self._finish(task, DownloadStatus.CANCELLED, error=result.error or "Cancelled by user")
        elif result.ok:
            self._finish(
                task, DownloadStatus.COMPLETED, file_path=result.file_path
            )
        else:
            self._finish(
                task, DownloadStatus.FAILED, error=result.error or "The download failed."
            )

    # ------------------------------------------------------------------ #
    # Status bookkeeping
    # ------------------------------------------------------------------ #

    def _preflight(self, request: DownloadRequest) -> Optional[str]:
        try:
            destination = validate_destination(Path(request.destination), create=True)
        except DestinationError as exc:
            return str(exc)

        if request.requires_ffmpeg and not self._ffmpeg().available:
            return (
                "FFmpeg is required for this download option (stream merging or "
                "conversion) but was not found. Install it with: brew install ffmpeg — "
                "or choose a quality that doesn't need FFmpeg."
            )

        try:
            ensure_free_space(destination, request.estimated_size)
        except InsufficientSpaceError as exc:
            return str(exc)
        return None

    def _create_history_row(self, task: _Task) -> None:
        if self._history is None:
            return
        request = task.request
        try:
            task.history_id = self._history.create(
                source_url=request.url,
                title=request.title,
                thumbnail_url=request.thumbnail_url,
                quality=request.quality_label,
                output_format=request.format_label,
                destination_path=str(request.destination),
                status="downloading",
            )
        except DatabaseError:
            # A history failure must never take down the actual download.
            logger.warning("Could not create history row for %s", task.id, exc_info=True)

    def _finish(
        self,
        task: _Task,
        status: DownloadStatus,
        *,
        error: Optional[str] = None,
        file_path: Optional[Path | str] = None,
    ) -> None:
        with self._lock:
            try:
                task.status = transition(task.status, status)
            except InvalidTransition:
                logger.warning(
                    "Task %s already %s; ignoring finish as %s",
                    task.id, task.status.value, status.value,
                )
                return
            task.error = error
            task.file_path = str(file_path) if file_path else None
            task.finished_at = now_iso()

        progress = task.progress
        if status is DownloadStatus.COMPLETED:
            task.progress = ProgressInfo(
                status=status,
                percent=100.0,
                downloaded_bytes=progress.downloaded_bytes or progress.total_bytes,
                total_bytes=progress.total_bytes,
                stage="Completed",
            )
        elif status is DownloadStatus.CANCELLED:
            task.progress = ProgressInfo(
                status=status, percent=progress.percent, stage="Cancelled"
            )
        else:
            task.progress = ProgressInfo(status=status, stage="Failed")

        self._update_history(task, status, error=error)
        self._post(task, force=True)

    def _update_history(
        self, task: _Task, status: DownloadStatus, *, error: Optional[str]
    ) -> None:
        if self._history is None or task.history_id is None:
            return
        try:
            self._history.update_status(
                task.history_id,
                status.value,
                completed_at=task.finished_at,
                error_message=error,
                destination_path=task.file_path,
            )
        except DatabaseError:
            logger.warning("Could not update history row %s", task.history_id, exc_info=True)

    def _post(self, task: _Task, *, force: bool = False) -> None:
        """Throttled snapshot publication; status changes always go out."""
        now = time.monotonic()
        status_changed = task._posted_status is not task.status
        if not force and not status_changed and (now - task._last_post) < _PROGRESS_POST_INTERVAL:
            return
        task._last_post = now
        task._posted_status = task.status
        self._bus.post(EVENT_DOWNLOAD, task.snapshot())

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #

    def shutdown(self, timeout: float = 3.0) -> int:
        """Cancel everything and wait briefly for workers to stop.

        Returns the number of workers that were still running when the
        timeout expired (rows left "downloading" are marked "interrupted"
        on the next launch).
        """
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            if task.status is DownloadStatus.QUEUED and self._scheduler.remove_queued(task.id):
                try:
                    task.status = transition(task.status, DownloadStatus.CANCELLED)
                except InvalidTransition:  # pragma: no cover - defensive
                    pass
            elif task.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING):
                task.cancel_event.set()

        deadline = time.monotonic() + timeout
        still_running = 0
        for task_id, thread in self._scheduler.active_threads().items():
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
            if thread.is_alive():
                still_running += 1
        if still_running:
            logger.warning(
                "%d download worker(s) still running at shutdown timeout", still_running
            )
        return still_running
