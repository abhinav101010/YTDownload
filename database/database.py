"""Database connection management and schema migration.

One SQLite connection is shared across the UI and worker threads; a lock
serialises access and short transactions keep the database consistent.
``PRAGMA user_version`` drives forward-only schema migrations so a database
created by an older build upgrades safely instead of failing to open.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Sequence

from app import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url      TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    thumbnail_url   TEXT,
    quality         TEXT,
    output_format   TEXT,
    destination_path TEXT,
    status          TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    completed_at    TEXT,
    error_message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_created_at ON history (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_status     ON history (status);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DatabaseError(RuntimeError):
    """Raised when the database cannot be initialised or queried."""


class Database:
    """Thread-safe thin wrapper around a single SQLite connection."""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path is not None else config.database_path()
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def open(self) -> "Database":
        with self._lock:
            if self._conn is not None:
                return self
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(
                    self.path,
                    check_same_thread=False,
                    timeout=10.0,
                    isolation_level=None,  # explicit transaction control
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                self._conn = conn
            except (sqlite3.Error, OSError) as exc:
                raise DatabaseError(f"Could not open the database at {self.path}: {exc}") from exc
            self._migrate()
            return self

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.commit()
                    self._conn.close()
                except sqlite3.Error:  # pragma: no cover - best effort on shutdown
                    logger.warning("Error while closing the database", exc_info=True)
                finally:
                    self._conn = None

    def _migrate(self) -> None:
        assert self._conn is not None
        with self._lock:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise DatabaseError(
                    f"This database was created by a newer version of the app "
                    f"(schema {version} > {SCHEMA_VERSION})."
                )
            if version < 1:
                self._conn.executescript(_SCHEMA_V1)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                logger.info("Initialised database schema at %s", self.path)
            # Future migrations: elif version < 2: ... etc.

    # ------------------------------------------------------------------ #
    # Query helpers (all serialised through the lock)
    # ------------------------------------------------------------------ #

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def execute(self, sql: str, params: Sequence = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                return self.connection.execute(sql, tuple(params))
            except sqlite3.Error as exc:
                logger.error("SQL failed: %s (%s)", sql.split("\n")[0][:120], params)
                raise DatabaseError(str(exc)) from exc

    def query_all(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
        return list(self.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence = ()) -> Optional[sqlite3.Row]:
        return self.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit ``BEGIN IMMEDIATE`` … ``COMMIT`` transaction."""
        with self._lock:
            conn = self.connection
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def integrity_ok(self) -> bool:
        row = self.query_one("PRAGMA quick_check")
        return bool(row) and str(row[0]).lower() == "ok"
