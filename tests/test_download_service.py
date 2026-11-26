"""Download service orchestration: queueing, preflight, cancel/retry, history.

Everything here runs offline: the engine is stubbed per test with behaviours
that succeed, fail, block until cancelled, or never start.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.state import EVENT_DOWNLOAD, EventBus
from downloader.engine import DownloadRequest, EngineResult
from downloader.progress import DownloadStatus, TERMINAL_STATUSES
from services.download_service import DownloadService
from tests.helpers import (
    blocking_engine,
    failing_engine,
    make_request,
    make_service,
    success_engine,
    wait_status,
    wait_terminal,
)


def drain_events(bus: EventBus):
    return bus.drain(256)


class TestSubmit:
    def test_submit_accepts_and_emits_queued_snapshot(self, tmp_path, bus):
        service, _ = make_service(tmp_path, bus=bus, run=success_engine(tmp_path))
        ok, task_id = service.submit(make_request(tmp_path))
        assert ok
        # The queued event is posted synchronously by submit(), regardless of
        # how far the worker has already progressed.
        snapshots = [e.payload for e in drain_events(bus) if e.kind == EVENT_DOWNLOAD]
        assert snapshots and snapshots[0].status is DownloadStatus.QUEUED
        assert snapshots[0].id == task_id

        wait_terminal(service, task_id)
        assert service.get(task_id).status is DownloadStatus.COMPLETED
        service.shutdown()

    def test_duplicate_active_url_rejected(self, tmp_path):
        started, release = threading.Event(), threading.Event()
        service, _ = make_service(tmp_path, run=blocking_engine(started, release))
        try:
            ok1, first = service.submit(make_request(tmp_path, "https://example.com/v/x"))
            assert ok1
            wait_status(service, first, DownloadStatus.DOWNLOADING)

            ok2, reason = service.submit(
                make_request(tmp_path, "https://example.com/v/x")
            )
            assert not ok2
            assert "already" in reason.lower()

            # A different URL is fine while the first runs.
            ok3, _ = service.submit(make_request(tmp_path, "https://example.com/v/y"))
            assert ok3
        finally:
            release.set()
            service.shutdown(timeout=5)

    def test_same_url_allowed_again_after_terminal(self, tmp_path):
        service, _ = make_service(tmp_path, run=success_engine(tmp_path))
        url = "https://example.com/v/replay"
        _, first = service.submit(make_request(tmp_path, url))
        wait_terminal(service, first)
        ok, second = service.submit(make_request(tmp_path, url))
        assert ok and second != first
        wait_terminal(service, second)
        assert len(service.snapshots()) == 2


class TestSuccessPath:
    def test_completes_with_file_and_history(self, tmp_path, history, bus):
        service, _ = make_service(
            tmp_path, history=history, bus=bus, run=success_engine(tmp_path)
        )
        ok, task_id = service.submit(make_request(tmp_path))
        assert ok

        final = wait_terminal(service, task_id)
        assert final.status is DownloadStatus.COMPLETED
        assert final.error is None
        assert final.file_path and Path(final.file_path).is_file()
        assert final.progress.percent == 100.0

        # History row written and closed out as completed.
        assert final.history_id is not None
        record = history.get(final.history_id)
        assert record.status == "completed"
        # The row points at the finished file, not just the folder.
        assert record.destination_path == final.file_path
        assert Path(record.destination_path).is_file()
        assert record.title == "Sample video"

        # Events: queued -> downloading -> progress -> completed, in order.
        statuses = [
            e.payload.status
            for e in drain_events(bus)
            if e.kind == EVENT_DOWNLOAD
        ]
        assert statuses[0] is DownloadStatus.QUEUED
        assert DownloadStatus.DOWNLOADING in statuses
        assert statuses[-1] is DownloadStatus.COMPLETED
        assert statuses == sorted(statuses, key=lambda s: _progression(s))

    def test_snapshot_carries_quality_labels(self, tmp_path):
        service, _ = make_service(tmp_path, run=success_engine(tmp_path))
        request = make_request(
            tmp_path, quality_label="1080p", format_label="Merged → MP4"
        )
        _, task_id = service.submit(request)
        final = wait_terminal(service, task_id)
        assert final.quality_label == "1080p"
        assert final.format_label == "Merged → MP4"
        assert final.mode == "video"


class TestFailurePath:
    def test_failure_records_error_and_history(self, tmp_path, history, bus):
        service, _ = make_service(
            tmp_path, history=history, bus=bus, run=failing_engine("Nope, it broke")
        )
        _, task_id = service.submit(make_request(tmp_path))
        final = wait_terminal(service, task_id)

        assert final.status is DownloadStatus.FAILED
        assert "Nope, it broke" in final.error
        assert final.file_path is None

        record = history.get(final.history_id)
        assert record.status == "failed"

    def test_worker_crash_becomes_failure_not_hang(self, tmp_path):
        def boom(request, cancel_event, on_progress, on_stage=None):
            raise RuntimeError("engine exploded")

        service, _ = make_service(tmp_path, run=boom)
        _, task_id = service.submit(make_request(tmp_path))
        final = wait_terminal(service, task_id)
        assert final.status is DownloadStatus.FAILED
        assert "unexpected error" in final.error.lower()


class TestPreflight:
    def test_destination_that_is_a_file_fails_fast(self, tmp_path, history):
        blocker = tmp_path / "not-a-directory"
        blocker.write_bytes(b"x")
        calls: list = []

        def engine(request, cancel_event, on_progress, on_stage=None):
            calls.append(request)
            return EngineResult(ok=True)

        service, _ = make_service(tmp_path, history=history, run=engine)
        request = make_request(tmp_path, destination=blocker)
        _, task_id = service.submit(request)
        final = wait_terminal(service, task_id)

        assert final.status is DownloadStatus.FAILED
        assert "not a folder" in final.error.lower()
        assert calls == [], "engine must not run when preflight fails"
        assert history.get(final.history_id).status == "failed"

    def test_missing_ffmpeg_fails_with_actionable_hint(self, tmp_path, history):
        calls: list = []

        def engine(request, cancel_event, on_progress, on_stage=None):
            calls.append(request)
            return EngineResult(ok=True)

        service, _ = make_service(
            tmp_path, history=history, run=engine, ffmpeg_available=False
        )
        request = make_request(tmp_path, requires_ffmpeg=True)
        _, task_id = service.submit(request)
        final = wait_terminal(service, task_id)

        assert final.status is DownloadStatus.FAILED
        assert "FFmpeg" in final.error
        assert "brew install ffmpeg" in final.error
        assert calls == []

    def test_available_ffmpeg_passes_preflight(self, tmp_path):
        def engine(request, cancel_event, on_progress, on_stage=None):
            return EngineResult(ok=True, file_path=Path(request.destination) / "o.mp4")

        service, _ = make_service(tmp_path, run=engine, ffmpeg_available=True)
        _, task_id = service.submit(make_request(tmp_path, requires_ffmpeg=True))
        assert wait_terminal(service, task_id).status is DownloadStatus.COMPLETED


class TestCancellation:
    def test_cancel_queued_is_immediate(self, tmp_path, history, bus):
        started, release = threading.Event(), threading.Event()
        service, _ = make_service(
            tmp_path, history=history, run=blocking_engine(started, release)
        )
        try:
            _, running = service.submit(make_request(tmp_path, "https://example.com/v/a"))
            wait_status(service, running, DownloadStatus.DOWNLOADING)

            ok, queued = service.submit(make_request(tmp_path, "https://example.com/v/b"))
            assert ok
            assert service.get(queued).status is DownloadStatus.QUEUED

            accepted, message = service.cancel(queued)
            assert accepted
            assert "queue" in message.lower()

            snapshot = service.get(queued)
            assert snapshot.status is DownloadStatus.CANCELLED
            assert snapshot.error  # explains why it never produced a file
            assert snapshot.history_id is None  # never ran, so no history row
        finally:
            release.set()
            service.shutdown(timeout=5)

    def test_cancel_running_waits_for_worker_honesty(self, tmp_path, history):
        started, release = threading.Event(), threading.Event()
        service, _ = make_service(
            tmp_path, history=history, run=blocking_engine(started, release)
        )
        try:
            _, task_id = service.submit(make_request(tmp_path))
            wait_status(service, task_id, DownloadStatus.DOWNLOADING)

            accepted, message = service.cancel(task_id)
            assert accepted
            assert "stopping" in message.lower()
            # Still downloading — we were told it's stopping, not that it's done.
            assert service.get(task_id).status is DownloadStatus.DOWNLOADING

            final = wait_terminal(service, task_id, timeout=5)
            assert final.status is DownloadStatus.CANCELLED
            assert history.get(final.history_id).status == "cancelled"
        finally:
            release.set()
            service.shutdown(timeout=5)

    def test_cancel_unknown_task(self, tmp_path):
        service, _ = make_service(tmp_path)
        ok, reason = service.cancel("nope")
        assert not ok
        assert "no longer exists" in reason

    def test_cancel_finished_task_rejected(self, tmp_path):
        service, _ = make_service(tmp_path, run=success_engine(tmp_path))
        _, task_id = service.submit(make_request(tmp_path))
        wait_terminal(service, task_id)
        ok, reason = service.cancel(task_id)
        assert not ok
        assert "already finished" in reason


class TestRetry:
    def test_retry_requeues_and_creates_new_history_row(
        self, tmp_path, history, bus
    ):
        service, _ = make_service(
            tmp_path, history=history, bus=bus, run=failing_engine()
        )
        _, task_id = service.submit(make_request(tmp_path))
        first = wait_terminal(service, task_id)
        assert first.status is DownloadStatus.FAILED
        first_history_id = first.history_id

        ok, message = service.retry(task_id)
        assert ok and "queued" in message.lower()
        assert service.get(task_id).status in (
            DownloadStatus.QUEUED,
            DownloadStatus.DOWNLOADING,
        )

        second = wait_terminal(service, task_id)
        assert second.status is DownloadStatus.FAILED  # still failing, honestly
        assert second.history_id is not None
        assert second.history_id != first_history_id, "retry must open a new row"
        assert history.get(first_history_id).status == "failed"
        assert history.get(second.history_id).status == "failed"

    def test_retry_rejects_running_and_unknown(self, tmp_path, history):
        started, release = threading.Event(), threading.Event()
        service, _ = make_service(
            tmp_path, history=history, run=blocking_engine(started, release)
        )
        try:
            _, task_id = service.submit(make_request(tmp_path))
            wait_status(service, task_id, DownloadStatus.DOWNLOADING)
            ok, reason = service.retry(task_id)
            assert not ok and "Only failed or cancelled" in reason

            ok2, reason2 = service.retry("missing")
            assert not ok2 and "no longer exists" in reason2
        finally:
            release.set()
            service.shutdown(timeout=5)

    def test_retry_after_cancel_runs_again(self, tmp_path, history):
        started, release = threading.Event(), threading.Event()
        service, _ = make_service(
            tmp_path, history=history, run=blocking_engine(started, release)
        )
        try:
            _, task_id = service.submit(make_request(tmp_path))
            wait_status(service, task_id, DownloadStatus.DOWNLOADING)
            service.cancel(task_id)
            wait_terminal(service, task_id)

            # After shutdown of the engine behaviour, switch to success.
            release.set()
            service._engine.run = success_engine(tmp_path)  # type: ignore[method-assign]
            ok, _ = service.retry(task_id)
            assert ok
            final = wait_terminal(service, task_id)
            assert final.status is DownloadStatus.COMPLETED
        finally:
            service.shutdown(timeout=5)


class TestForgetAndClear:
    def test_forget_refuses_active_task(self, tmp_path, history):
        started, release = threading.Event(), threading.Event()
        service, _ = make_service(
            tmp_path, history=history, run=blocking_engine(started, release)
        )
        try:
            _, task_id = service.submit(make_request(tmp_path))
            wait_status(service, task_id, DownloadStatus.DOWNLOADING)
            assert service.forget(task_id) is False
        finally:
            release.set()
            service.shutdown(timeout=5)

    def test_forget_removes_terminal_task_keeps_history(self, tmp_path, history):
        service, _ = make_service(
            tmp_path, history=history, run=success_engine(tmp_path)
        )
        _, task_id = service.submit(make_request(tmp_path))
        final = wait_terminal(service, task_id)

        assert service.forget(task_id) is True
        assert service.get(task_id) is None
        assert history.get(final.history_id) is not None  # history is untouched

    def test_clear_finished_returns_count_and_keeps_active(self, tmp_path, history):
        done_service, _ = make_service(
            tmp_path, history=history, run=success_engine(tmp_path)
        )
        _, finished = done_service.submit(make_request(tmp_path))
        wait_terminal(done_service, finished)
        assert done_service.clear_finished() == 1
        assert done_service.snapshots() == []
        assert done_service.clear_finished() == 0  # idempotent


class TestConcurrencyLimit:
    def test_second_task_waits_for_a_slot(self, tmp_path, history):
        started, release = threading.Event(), threading.Event()
        service, _ = make_service(
            tmp_path,
            history=history,
            run=blocking_engine(started, release),
            max_concurrent=1,
        )
        try:
            _, blocker = service.submit(make_request(tmp_path, "https://example.com/v/a"))
            wait_status(service, blocker, DownloadStatus.DOWNLOADING)

            _, waiting = service.submit(make_request(tmp_path, "https://example.com/v/b"))
            time.sleep(0.15)
            assert service.get(waiting).status is DownloadStatus.QUEUED
            # Only the blocker has a worker thread (limit = 1); the waiter is
            # counted by the scheduler (active + queued = 2 pending).
            assert len(service._scheduler.active_threads()) == 1
            assert service.pending_count == 2

            # Free the slot: cancel the blocker, the waiter must start next.
            service.cancel(blocker)
            wait_terminal(service, blocker)
            wait_status(service, waiting, DownloadStatus.DOWNLOADING)
        finally:
            release.set()
            service.shutdown(timeout=5)

    def test_settings_change_can_raise_the_limit(self, tmp_path, history):
        started, release = threading.Event(), threading.Event()
        from app import config
        from app.state import AppSettings
        from services.media_service import FFmpegInfo

        settings = AppSettings.from_dict(
            {
                **config.DEFAULT_SETTINGS,
                "download_dir": str(tmp_path),
                "max_concurrent": "1",
            }
        )
        service = DownloadService(
            settings_provider=lambda: settings,
            event_bus=EventBus(),
            ffmpeg_provider=lambda: FFmpegInfo(False),
            history=history,
        )
        service._engine.run = blocking_engine(started, release)  # type: ignore[method-assign]
        try:
            _, a = service.submit(make_request(tmp_path, "https://example.com/v/a"))
            wait_status(service, a, DownloadStatus.DOWNLOADING)
            _, b = service.submit(make_request(tmp_path, "https://example.com/v/b"))
            time.sleep(0.1)
            assert service.get(b).status is DownloadStatus.QUEUED

            # Raising the limit applies immediately (settings are immutable —
            # replace() returns a new object, which the provider then sees).
            settings = settings.replace(max_concurrent=2)
            service.on_settings_changed()
            wait_status(service, b, DownloadStatus.DOWNLOADING)
            assert len(service._scheduler.active_threads()) == 2
        finally:
            release.set()
            service.shutdown(timeout=5)


class TestShutdown:
    def test_shutdown_cancels_queued_and_running(self, tmp_path, history):
        started, release = threading.Event(), threading.Event()
        service, _ = make_service(
            tmp_path, history=history, run=blocking_engine(started, release)
        )
        _, running = service.submit(make_request(tmp_path, "https://example.com/v/a"))
        wait_status(service, running, DownloadStatus.DOWNLOADING)
        _, queued = service.submit(make_request(tmp_path, "https://example.com/v/b"))
        assert service.get(queued).status is DownloadStatus.QUEUED

        still_running = service.shutdown(timeout=5)

        assert still_running == 0
        assert service.get(running).status is DownloadStatus.CANCELLED
        assert service.get(queued).status is DownloadStatus.CANCELLED
        assert service.active_count == 0

    def test_shutdown_with_nothing_running_is_clean(self, tmp_path):
        service, _ = make_service(tmp_path)
        assert service.shutdown() == 0


def _progression(status: DownloadStatus) -> int:
    order = {
        DownloadStatus.QUEUED: 0,
        DownloadStatus.DOWNLOADING: 1,
        DownloadStatus.PROCESSING: 2,
        DownloadStatus.COMPLETED: 3,
        DownloadStatus.FAILED: 3,
        DownloadStatus.CANCELLED: 3,
    }
    return order[status]
