"""Queue scheduler: FIFO order, bounded concurrency, launch bookkeeping."""

from __future__ import annotations

import threading

from services.queue_service import TaskScheduler


class Harness:
    """Launch helper that registers threads with the scheduler (its contract)."""

    def __init__(self, scheduler: TaskScheduler):
        self.scheduler = scheduler
        self.started: list[str] = []
        self.finished: list[str] = []
        self.gate = threading.Event()
        self._lock = threading.Lock()

    def launch(self, task_id: str) -> None:
        def worker() -> None:
            self.gate.wait(5)
            with self._lock:
                self.finished.append(task_id)

        with self._lock:
            self.started.append(task_id)
        thread = threading.Thread(target=worker, daemon=True)
        self.scheduler.activate(task_id, thread)
        thread.start()

    def stop_all(self) -> None:
        self.gate.set()
        for thread in list(self.scheduler.active_threads().values()):
            thread.join(timeout=5)


def test_fifo_order_with_concurrency_one():
    scheduler = TaskScheduler(max_concurrency=lambda: 1)
    harness = Harness(scheduler)
    for task_id in ("a", "b", "c"):
        assert scheduler.enqueue(task_id) is True

    assert scheduler.pump(harness.launch) == 1
    assert harness.started == ["a"]
    assert scheduler.queued_ids == ["b", "c"]
    assert scheduler.active_ids == ["a"]

    # Releasing the slot lets exactly one more start.
    scheduler.release("a")
    assert scheduler.pump(harness.launch) == 1
    assert harness.started == ["a", "b"]

    harness.stop_all()


def test_concurrency_limit_is_never_exceeded():
    scheduler = TaskScheduler(max_concurrency=lambda: 2)
    harness = Harness(scheduler)
    for task_id in ("a", "b", "c", "d"):
        scheduler.enqueue(task_id)

    assert scheduler.pump(harness.launch) == 2
    assert len(scheduler.active_ids) == 2
    # Pumping again while at the limit starts nothing.
    assert scheduler.pump(harness.launch) == 0
    assert scheduler.queued_ids == ["c", "d"]

    scheduler.release("a")
    assert scheduler.pump(harness.launch) == 1
    assert sorted(scheduler.active_ids) == ["b", "c"]

    harness.stop_all()


def test_enqueue_duplicates_rejected():
    scheduler = TaskScheduler(max_concurrency=lambda: 1)
    assert scheduler.enqueue("a") is True
    assert scheduler.enqueue("a") is False
    assert scheduler.queued_ids == ["a"]


def test_remove_queued():
    scheduler = TaskScheduler(max_concurrency=lambda: 1)
    scheduler.enqueue("a")
    scheduler.enqueue("b")

    assert scheduler.remove_queued("b") is True
    assert scheduler.remove_queued("b") is False      # already gone
    assert scheduler.remove_queued("never") is False  # never existed

    # An active task cannot be "removed from the queue".
    harness = Harness(scheduler)
    scheduler.pump(harness.launch)
    assert scheduler.remove_queued("a") is False
    harness.stop_all()


def test_pending_helpers():
    scheduler = TaskScheduler(max_concurrency=lambda: 1)
    harness = Harness(scheduler)
    scheduler.enqueue("a")
    scheduler.enqueue("b")

    assert scheduler.is_pending("a") is True
    assert scheduler.is_pending("b") is True
    assert scheduler.is_pending("zz") is False
    assert scheduler.pending_count == 2
    assert scheduler.queued_count == 2   # nothing has started yet
    assert scheduler.active_count == 0

    assert scheduler.pump(harness.launch) == 1
    assert scheduler.active_count == 1
    assert scheduler.queued_count == 1
    assert scheduler.pending_count == 2  # pending = queued + active

    scheduler.release("a")
    harness.stop_all()


def test_launch_failure_does_not_break_pump():
    scheduler = TaskScheduler(max_concurrency=lambda: 3)
    harness = Harness(scheduler)

    def exploding_launch(task_id: str) -> None:
        raise RuntimeError("boom")

    scheduler.enqueue("bad")
    scheduler.enqueue("good")
    started = scheduler.pump(
        lambda task_id: exploding_launch(task_id) if task_id == "bad" else harness.launch(task_id)
    )

    assert started == 1                 # only "good" started
    assert scheduler.queued_ids == []   # "bad" was consumed (and logged)
    assert harness.started == ["good"]
    harness.stop_all()


def test_active_threads_returns_a_copy():
    scheduler = TaskScheduler(max_concurrency=lambda: 1)
    harness = Harness(scheduler)
    scheduler.enqueue("a")
    scheduler.pump(harness.launch)

    snapshot = scheduler.active_threads()
    snapshot.clear()  # mutating the copy must not corrupt the scheduler
    assert scheduler.active_ids == ["a"]
    harness.stop_all()


def test_scheduler_snapshot():
    scheduler = TaskScheduler(max_concurrency=lambda: 2)
    harness = Harness(scheduler)
    scheduler.enqueue("a")
    scheduler.enqueue("b")
    scheduler.pump(harness.launch)

    snapshot = scheduler.snapshot()
    assert snapshot["queued"] == []
    assert sorted(snapshot["active"]) == ["a", "b"]
    assert snapshot["limit"] == 2
    harness.stop_all()
