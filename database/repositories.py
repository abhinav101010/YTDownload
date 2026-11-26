"""Repositories: parameterised SQL for history and settings.

All queries use bound parameters.  Repositories never touch Tk and never
perform filesystem checks (``HistoryRecord.file_state`` does that lazily).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

from app import config
from database.database import Database, DatabaseError
from database.models import HISTORY_STATUSES, HistoryRecord, now_iso

logger = logging.getLogger(__name__)

# Statuses that can only exist while a worker is running.  If the app starts
# with rows still in one of these states, the previous run crashed or was
# force-quit; they are marked "interrupted" instead of lying as "downloading".
_IN_FLIGHT_STATUSES = ("downloading", "queued", "processing")

_HISTORY_COLUMNS = (
    "id, source_url, title, thumbnail_url, quality, output_format, "
    "destination_path, status, created_at, completed_at, error_message"
)


def _row_to_record(row: sqlite3.Row) -> HistoryRecord:
    return HistoryRecord(
        id=int(row["id"]),
        source_url=str(row["source_url"]),
        title=str(row["title"]),
        thumbnail_url=row["thumbnail_url"],
        quality=row["quality"],
        output_format=row["output_format"],
        destination_path=row["destination_path"],
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        completed_at=row["completed_at"],
        error_message=row["error_message"],
    )


class HistoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        source_url: str,
        title: str,
        thumbnail_url: Optional[str] = None,
        quality: Optional[str] = None,
        output_format: Optional[str] = None,
        destination_path: Optional[str] = None,
        status: str = "downloading",
        created_at: Optional[str] = None,
    ) -> int:
        if status not in HISTORY_STATUSES:
            raise ValueError(f"Unknown history status: {status!r}")
        cursor = self._db.execute(
            "INSERT INTO history (source_url, title, thumbnail_url, quality, "
            "output_format, destination_path, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_url,
                title,
                thumbnail_url,
                quality,
                output_format,
                destination_path,
                status,
                created_at or now_iso(),
            ),
        )
        return int(cursor.lastrowid)

    def update_status(
        self,
        record_id: int,
        status: str,
        *,
        completed_at: Optional[str] = None,
        error_message: Optional[str] = None,
        destination_path: Optional[str] = None,
    ) -> bool:
        if status not in HISTORY_STATUSES:
            raise ValueError(f"Unknown history status: {status!r}")
        sets = ["status = ?"]
        params: list[Any] = [status]
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        if error_message is not None:
            sets.append("error_message = ?")
            params.append(error_message)
        if destination_path is not None:
            sets.append("destination_path = ?")
            params.append(destination_path)
        params.append(record_id)
        cursor = self._db.execute(
            f"UPDATE history SET {', '.join(sets)} WHERE id = ?", params
        )
        return cursor.rowcount > 0

    def get(self, record_id: int) -> Optional[HistoryRecord]:
        row = self._db.query_one(
            f"SELECT {_HISTORY_COLUMNS} FROM history WHERE id = ?", (record_id,)
        )
        return _row_to_record(row) if row else None

    def list(
        self,
        *,
        search: str = "",
        status: Optional[str] = None,
        limit: int = 500,
    ) -> list[HistoryRecord]:
        sql = [f"SELECT {_HISTORY_COLUMNS} FROM history"]
        where: list[str] = []
        params: list[Any] = []

        term = (search or "").strip()
        if term:
            where.append("(title LIKE ? ESCAPE '\\' OR source_url LIKE ? ESCAPE '\\')")
            pattern = f"%{_escape_like(term)}%"
            params.extend([pattern, pattern])

        if status and status != "all":
            if status not in HISTORY_STATUSES:
                raise ValueError(f"Unknown history status: {status!r}")
            where.append("status = ?")
            params.append(status)

        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY created_at DESC, id DESC LIMIT ?")
        params.append(int(limit))

        return [_row_to_record(row) for row in self._db.query_all(" ".join(sql), params)]

    def count(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM history")
        return int(row["n"]) if row else 0

    def delete(self, record_id: int) -> bool:
        """Remove one history entry. Never touches the downloaded file."""
        cursor = self._db.execute("DELETE FROM history WHERE id = ?", (record_id,))
        return cursor.rowcount > 0

    def clear(self) -> int:
        """Remove all history entries. Never touches downloaded files."""
        cursor = self._db.execute("DELETE FROM history")
        return cursor.rowcount

    def recover_stale(self) -> int:
        """Mark in-flight rows from a previous run as "interrupted"."""
        with self._db.transaction():
            cursor = self._db.connection.execute(
                "UPDATE history SET status = 'interrupted', "
                "completed_at = NULL, "
                "error_message = 'The application closed before this download finished.' "
                f"WHERE status IN ({','.join('?' * len(_IN_FLIGHT_STATUSES))})",
                _IN_FLIGHT_STATUSES,
            )
            changed = cursor.rowcount
        if changed:
            logger.info("Marked %d stale history row(s) as interrupted", changed)
        return int(changed)


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def load(self) -> dict[str, str]:
        rows = self._db.query_all("SELECT key, value FROM settings")
        raw = {str(row["key"]): str(row["value"]) for row in rows}
        return config.sanitize_settings(raw)

    def save(self, values: dict[str, str]) -> None:
        """Upsert settings (invalid keys are ignored, values sanitised)."""
        cleaned = config.sanitize_settings(values)
        with self._db.transaction():
            conn = self._db.connection
            for key, value in cleaned.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    def reset(self) -> dict[str, str]:
        with self._db.transaction():
            self._db.connection.execute("DELETE FROM settings")
        return dict(config.DEFAULT_SETTINGS)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return config.DEFAULT_SETTINGS.get(key, default)
        return config.sanitize_settings({key: str(row["value"])}).get(key, default)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
