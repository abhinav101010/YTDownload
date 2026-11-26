"""SQLite persistence: history, settings sanitisation, migrations, threads."""

from __future__ import annotations

import threading

import pytest

from app import config
from database.database import Database, DatabaseError, SCHEMA_VERSION
from database.models import HistoryRecord
from database.repositories import HistoryRepository, SettingsRepository


def create(history: HistoryRepository, **overrides) -> int:
    payload = dict(
        source_url="https://example.com/watch?v=1",
        title="Sample",
        quality="1080p",
        output_format="Merged → MP4",
        destination_path="/tmp/out.mp4",
        status="completed",
    )
    payload.update(overrides)
    return history.create(**payload)


class TestSchema:
    def test_fresh_database_gets_current_schema_version(self, tmp_path):
        path = tmp_path / "fresh.db"
        database = Database(path).open()
        try:
            version = database.query_one("PRAGMA user_version")[0]
        finally:
            database.close()
        assert int(version) == SCHEMA_VERSION

    def test_reopen_is_idempotent_and_preserves_data(self, db):
        create(HistoryRepository(db))
        second = Database(db.path)
        second.open()
        try:
            rows = second.query_all("SELECT id FROM history")
            assert len(rows) >= 1
        finally:
            second.close()

    def test_future_schema_version_refused(self, tmp_path):
        path = tmp_path / "future.db"
        database = Database(path).open()
        database.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        database.close()
        with pytest.raises(DatabaseError, match="schema"):
            Database(path).open()


class TestHistoryRepository:
    def test_create_get_round_trip(self, history):
        record_id = create(
            history,
            title="Hello world",
            thumbnail_url="https://example.com/thumb.jpg",
            status="completed",
        )
        record = history.get(record_id)
        assert isinstance(record, HistoryRecord)
        assert record.title == "Hello world"
        assert record.thumbnail_url == "https://example.com/thumb.jpg"
        assert record.status == "completed"
        assert record.source_url.startswith("https://example.com/")

    def test_get_missing_returns_none(self, history):
        assert history.get(9999) is None

    def test_count_and_clear(self, history):
        assert history.count() == 0
        create(history)
        create(history, title="Second")
        assert history.count() == 2
        assert history.clear() == 2
        assert history.count() == 0
        assert history.clear() == 0

    def test_delete_only_removes_the_row(self, history, tmp_path):
        target = tmp_path / "keep-me.mp4"
        target.write_bytes(b"data")
        record_id = create(history, destination_path=str(target))
        assert history.delete(record_id) is True
        assert history.get(record_id) is None
        assert history.delete(record_id) is False  # already gone
        assert target.is_file()  # deleting history never deletes the file

    def test_list_orders_newest_first(self, history):
        older = create(history, title="Older", created_at="2026-01-01T00:00:00")
        newer = create(history, title="Newer", created_at="2026-06-01T00:00:00")
        titles = [record.title for record in history.list()]
        assert titles == ["Newer", "Older"]
        assert [record.id for record in history.list()] == [newer, older]

    def test_search_matches_title_or_url(self, history):
        create(history, title="Cats being cute", source_url="https://a.example/1")
        create(history, title="Dogs", source_url="https://b.example/cats-path")
        assert len(history.list(search="cats")) == 2
        assert [r.title for r in history.list(search="Cute")] == ["Cats being cute"]
        assert [r.title for r in history.list(search="b.example")] == ["Dogs"]
        assert history.list(search="no match at all") == []

    def test_search_escapes_like_wildcards(self, history):
        create(history, title="100% legit video")
        create(history, title="50 percent video")
        # A bare "%" must act as a literal, not match everything.
        matches = [r.title for r in history.list(search="%")]
        assert matches == ["100% legit video"]
        # "_" must be literal too (otherwise it would match any 1-char term).
        create(history, title="a_b file")
        assert [r.title for r in history.list(search="a_b")] == ["a_b file"]

    def test_status_filter(self, history):
        create(history, title="Ok", status="completed")
        create(history, title="Bad", status="failed")
        assert [r.title for r in history.list(status="failed")] == ["Bad"]
        assert len(history.list(status="all")) == 2
        assert len(history.list(status=None)) == 2

    def test_unknown_status_filter_raises(self, history):
        with pytest.raises(ValueError, match="Unknown history status"):
            history.list(status="exploded")

    def test_unknown_status_on_create_raises(self, history):
        with pytest.raises(ValueError, match="Unknown history status"):
            create(history, status="exploded")

    def test_update_status_records_outcome(self, history, tmp_path):
        record_id = create(history, status="downloading")
        target = tmp_path / "done.mp4"
        target.write_bytes(b"x")
        assert history.update_status(
            record_id,
            "completed",
            completed_at="2026-10-19T12:00:00",
            destination_path=str(target),
        )
        record = history.get(record_id)
        assert record.status == "completed"
        assert record.completed_at == "2026-10-19T12:00:00"
        assert record.destination_path == str(target)
        assert record.file_state() == "available"

    def test_update_status_missing_row_returns_false(self, history):
        assert history.update_status(4242, "completed") is False

    def test_recover_stale_marks_running_downloads_interrupted(self, history):
        running = create(history, title="Was running", status="downloading")
        done = create(history, title="Finished", status="completed")
        cancelled = create(history, title="Cancelled", status="cancelled")

        recovered = history.recover_stale()

        assert recovered == 1
        assert history.get(running).status == "interrupted"
        assert history.get(done).status == "completed"
        assert history.get(cancelled).status == "cancelled"

    def test_recover_stale_is_idempotent(self, history):
        create(history, status="downloading")
        assert history.recover_stale() == 1
        assert history.recover_stale() == 0

    def test_file_state_and_staleness(self, history, tmp_path):
        exists = tmp_path / "here.mp4"
        exists.write_bytes(b"x")
        available = create(history, destination_path=str(exists))
        assert history.get(available).file_state() == "available"
        assert history.get(available).is_stale is False

        missing = create(history, destination_path=str(tmp_path / "gone.mp4"))
        assert history.get(missing).file_state() == "missing"
        assert history.get(missing).is_stale is True

        unknown = create(history, destination_path=None)
        assert history.get(unknown).file_state() == "missing"

        broken = create(history, status="failed", destination_path=str(tmp_path))
        assert history.get(broken).file_state() == "unknown"  # never completed


class TestSettingsRepository:
    def test_defaults_on_empty_database(self, settings_repo):
        loaded = settings_repo.load()
        assert loaded == config.sanitize_settings({})

    def test_round_trip(self, settings_repo):
        values = dict(config.DEFAULT_SETTINGS, appearance="Light", max_concurrent="3")
        settings_repo.save(values)
        loaded = settings_repo.load()
        assert loaded["appearance"] == "Light"
        assert loaded["max_concurrent"] == "3"

    def test_invalid_values_sanitised_on_save(self, settings_repo):
        settings_repo.save(
            {
                **config.DEFAULT_SETTINGS,
                "appearance": "Neon",
                "max_concurrent": "99",
                "default_mode": "telepathy",
                "check_updates_on_start": "maybe",
                "download_dir": "",  # empty folder falls back to the default
            }
        )
        loaded = settings_repo.load()
        assert loaded["appearance"] == config.DEFAULT_SETTINGS["appearance"]
        assert loaded["max_concurrent"] == config.DEFAULT_SETTINGS["max_concurrent"]
        assert loaded["default_mode"] == "video"
        assert loaded["check_updates_on_start"] == "1"
        assert loaded["download_dir"] == config.DEFAULT_SETTINGS["download_dir"]

    def test_unknown_keys_are_dropped(self, settings_repo):
        settings_repo.save({**config.DEFAULT_SETTINGS, "evil_key": "1"})
        assert "evil_key" not in settings_repo.load()

    def test_hand_edited_database_is_sanitised_on_load(self, settings_repo, db):
        # Simulate corruption/editing by writing raw rows directly.
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("appearance", "Plaid"),
        )
        assert settings_repo.load()["appearance"] == config.DEFAULT_SETTINGS["appearance"]

    def test_reset_restores_defaults(self, settings_repo):
        settings_repo.save(dict(config.DEFAULT_SETTINGS, appearance="Light"))
        reset = settings_repo.reset()
        assert reset == settings_repo.load()
        assert reset["appearance"] == config.DEFAULT_SETTINGS["appearance"]


class TestConcurrency:
    def test_parallel_inserts_are_serialised(self, history):
        errors: list[BaseException] = []

        def writer(worker: int) -> None:
            try:
                for index in range(5):
                    create(history, title=f"w{worker}-{index}")
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors, errors
        assert history.count() == 40
