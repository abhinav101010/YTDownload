"""Bounded FIFO task scheduler for downloads.

Keeps queue bookkeeping (ordering, duplicate suppression hooks, the
concurrency limit, active-thread bookkeeping) separate from the download
engine so it can be unit-tested without network or yt-dlp.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class TaskScheduler:
    """FIFO queue + bounded set of running worker threads."""

    def __init__(self, *, max_concurrency: Callable[[], int] = lambda: 1) -> None:
        self._lock = threading.RLock()
        self._queued: deque[str] = deque()
        self._active: dict[str, threading.Thread] = {}
        self._max_concurrency = max_concurrency

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    @property
    def queued_ids(self) -> list[str]:
        with self._lock:
            return list(self._queued)

    @property
    def active_ids(self) -> list[str]:
        with self._lock:
            return list(self._active)

    @property
    def queued_count(self) -> int:
        with self._lock:
            return len(self._queued)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def pending_count(self) -> int:
        """Queued + running — used to warn before quitting."""
        return self.queued_count + self.active_count

    def is_pending(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._queued or task_id in self._active

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #

    def enqueue(self, task_id: str) -> bool:
        """Add to the queue. Returns False if it is already pending."""
        with self._lock:
            if task_id in self._queued or task_id in self._active:
                return False
            self._queued.append(task_id)
            return True

    def remove_queued(self, task_id: str) -> bool:
        """Remove a not-yet-started task. Returns True when it was queued."""
        with self._lock:
            try:
                self._queued.remove(task_id)
                return True
            except ValueError:
                return False

    def activate(self, task_id: str, thread: threading.Thread) -> None:
        with self._lock:
            self._active[task_id] = thread

    def release(self, task_id: str) -> None:
        with self._lock:
            self._active.pop(task_id, None)

    def pump(self, launch: Callable[[str], None]) -> int:
        """Start queued tasks while below the concurrency limit.

        ``launch(task_id)`` must synchronously register the worker via
        :meth:`activate` before returning (the download service starts a
        daemon thread and registers it).
        """
        started = 0
        with self._lock:
            limit = max(1, int(self._max_concurrency()))
            while self._queued and len(self._active) < limit:
                task_id = self._queued.popleft()
                if task_id in self._active:
                    continue
                try:
                    launch(task_id)
                except Exception:
                    logger.exception("Failed to launch task %s", task_id)
                    continue
                started += 1
        return started

    def snapshot(self) -> dict[str, list[str] | int]:
        with self._lock:
            return {
                "queued": list(self._queued),
                "active": list(self._active),
                "limit": max(1, int(self._max_concurrency())),
            }

    def active_threads(self) -> dict[str, threading.Thread]:
        """Copy of the active task-id → worker-thread mapping."""
        with self._lock:
            return dict(self._active)
