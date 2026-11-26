"""Fixtures: isolated database, repositories and event bus.

No test in this suite touches the network — the engine is replaced by callables
from ``tests.helpers`` that emulate outcomes (success, failure, blocking until
cancelled). The root conftest.py points ``YTDL_APP_DATA_DIR`` at a temp dir so
nothing here can touch real user data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.state import EventBus
from database.database import Database
from database.repositories import HistoryRepository, SettingsRepository


@pytest.fixture()
def db(tmp_path: Path):
    database = Database(tmp_path / "test.db").open()
    yield database
    database.close()


@pytest.fixture()
def history(db: Database) -> HistoryRepository:
    return HistoryRepository(db)


@pytest.fixture()
def settings_repo(db: Database) -> SettingsRepository:
    return SettingsRepository(db)


@pytest.fixture()
def bus() -> EventBus:
    return EventBus()
