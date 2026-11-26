"""Application configuration: defaults, sanitisation, paths, logging."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config


class TestDefaults:
    def test_every_setting_has_a_default(self):
        assert set(config.DEFAULT_SETTINGS) == {
            "download_dir",
            "default_mode",
            "preferred_quality",
            "video_container",
            "appearance",
            "accent_theme",
            "max_concurrent",
            "ffmpeg_path",
            "check_updates_on_start",
        }

    def test_defaults_are_valid_against_their_own_enums(self):
        cleaned = config.sanitize_settings(dict(config.DEFAULT_SETTINGS))
        assert cleaned == config.DEFAULT_SETTINGS

    def test_default_download_dir_is_absolute(self):
        directory = config.default_download_dir()
        assert directory.is_absolute()
        assert "Downloads" in str(directory)

    def test_max_url_length_is_sane(self):
        assert 100 < config.MAX_URL_LENGTH <= 8192


class TestSanitizeSettings:
    def test_missing_values_fall_back_to_defaults(self):
        assert config.sanitize_settings({}) == config.DEFAULT_SETTINGS

    def test_unknown_enum_values_rejected(self):
        cleaned = config.sanitize_settings(
            {
                **config.DEFAULT_SETTINGS,
                "appearance": "Neon",
                "default_mode": "telepathy",
                "video_container": "avi",
                "accent_theme": "rainbow",
            }
        )
        assert cleaned["appearance"] == "Dark"
        assert cleaned["default_mode"] == "video"
        assert cleaned["video_container"] == "mp4"
        assert cleaned["accent_theme"] == "blue"

    def test_concurrency_out_of_range_rejected(self):
        assert (
            config.sanitize_settings(
                {**config.DEFAULT_SETTINGS, "max_concurrent": "99"}
            )["max_concurrent"]
            == "1"
        )
        assert (
            config.sanitize_settings(
                {**config.DEFAULT_SETTINGS, "max_concurrent": "3"}
            )["max_concurrent"]
            == "3"
        )
        assert (
            config.sanitize_settings(
                {**config.DEFAULT_SETTINGS, "max_concurrent": "lots"}
            )["max_concurrent"]
            == "1"
        )

    def test_boolean_setting_constrained(self):
        assert (
            config.sanitize_settings(
                {**config.DEFAULT_SETTINGS, "check_updates_on_start": "maybe"}
            )["check_updates_on_start"]
            == "1"
        )
        assert (
            config.sanitize_settings(
                {**config.DEFAULT_SETTINGS, "check_updates_on_start": "0"}
            )["check_updates_on_start"]
            == "0"
        )

    def test_non_string_values_rejected(self):
        cleaned = config.sanitize_settings(
            {**config.DEFAULT_SETTINGS, "max_concurrent": 4, "appearance": 1}  # type: ignore[dict-item]
        )
        assert cleaned["max_concurrent"] == "1"
        assert cleaned["appearance"] == "Dark"

    def test_empty_download_dir_falls_back(self):
        cleaned = config.sanitize_settings(
            {**config.DEFAULT_SETTINGS, "download_dir": ""}
        )
        assert cleaned["download_dir"] == config.DEFAULT_SETTINGS["download_dir"]

    def test_unknown_keys_dropped(self):
        cleaned = config.sanitize_settings(
            {**config.DEFAULT_SETTINGS, "backdoor": "1"}
        )
        assert "backdoor" not in cleaned


class TestPaths:
    def test_app_data_dir_respects_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YTDL_APP_DATA_DIR", str(tmp_path))
        config.app_data_dir.cache_clear()
        try:
            assert config.app_data_dir() == tmp_path
            assert tmp_path.is_dir()
        finally:
            # Restore for every other test (the env var itself is restored by
            # monkeypatch; the cache is not).
            config.app_data_dir.cache_clear()

    def test_log_file_path_lives_under_app_data_dir(self):
        log_path = config.log_file_path()
        assert log_path.suffix == ".log"
        assert config.app_data_dir() in log_path.parents

    def test_setup_logging_creates_the_file(self, tmp_path):
        # setup_logging() is intentionally idempotent (it returns early when the
        # root logger already has handlers — as under pytest), so verify the
        # fresh-start path in a clean subprocess.
        import os
        import subprocess
        import sys

        root = Path(__file__).resolve().parent.parent
        env = {**os.environ, "YTDL_APP_DATA_DIR": str(tmp_path)}
        script = (
            "from app import config; config.setup_logging(verbose=True); "
            "print(config.log_file_path())"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        logged_path = Path(completed.stdout.strip().splitlines()[-1])
        assert logged_path.exists()
        assert logged_path.parent.parent == tmp_path

    def test_thumbnail_cache_dir_created_on_demand(self):
        cache = config.thumbnail_cache_dir()
        assert cache.is_dir()
        assert config.app_data_dir() in cache.parents
