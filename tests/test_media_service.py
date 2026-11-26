"""FFmpeg detection, install hints and yt-dlp version helpers (offline)."""

from __future__ import annotations

import shutil
import stat

import pytest

from services import media_service
from services.media_service import (
    FFmpegInfo,
    _naive_version,
    _run_version,
    detect_ffmpeg,
    ffmpeg_install_hint,
    yt_dlp_version,
)


class TestFFmpegInfo:
    def test_defaults_are_unavailable(self):
        info = FFmpegInfo(available=False)
        assert info.path is None
        assert info.source == "none"
        assert "not found" in info.label.lower()

    def test_label_includes_path_and_version(self):
        info = FFmpegInfo(True, "/opt/bin/ffmpeg", "8.1.2", "configured")
        assert "/opt/bin/ffmpeg" in info.label
        assert "8.1.2" in info.label
        assert "found" in info.label.lower()


class TestDetectFFmpeg:
    def test_bogus_configured_path_falls_back_to_path_search(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(media_service.shutil, "which", lambda _name: None)
        info = detect_ffmpeg(str(tmp_path / "does-not-exist"))
        assert info.available is False
        assert info.error

    def test_no_ffmpeg_anywhere_reports_unavailable(self, monkeypatch):
        monkeypatch.setattr(media_service.shutil, "which", lambda _name: None)
        info = detect_ffmpeg("")
        assert info.available is False
        assert info.source == "none"
        assert info.path is None

    def test_configured_executable_is_used_when_runnable(self, tmp_path, monkeypatch):
        fake = tmp_path / "ffmpeg"
        fake.write_text("#!/bin/sh\necho 'ffmpeg version 9.9-test Copyright (c) x'\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        # Make sure PATH discovery can't shadow the result.
        monkeypatch.setattr(
            media_service.shutil, "which", lambda _name: str(tmp_path / "other")
        )

        info = detect_ffmpeg(str(fake))
        assert info.available is True
        assert info.source == "configured"
        assert info.path == str(fake)
        assert "9.9" in (info.version or "")

    def test_configured_directory_containing_ffmpeg(self, tmp_path, monkeypatch):
        fake = tmp_path / "ffmpeg"
        fake.write_text("#!/bin/sh\necho 'ffmpeg version 6.0-test Copyright'\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(media_service.shutil, "which", lambda _name: None)

        info = detect_ffmpeg(str(tmp_path))  # the directory, not the binary
        assert info.available is True
        assert info.path == str(fake)

    def test_unrunnable_binary_falls_through(self, tmp_path, monkeypatch):
        broken = tmp_path / "ffmpeg"
        broken.write_text("this is not a script\n")
        broken.chmod(broken.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(media_service.shutil, "which", lambda _name: None)

        info = detect_ffmpeg(str(broken))
        assert info.available is False

    def test_run_version_rejects_missing_executable(self):
        version, error = _run_version("/definitely/not/here/ffmpeg")
        assert version is None
        assert error


class TestVersionHelpers:
    @pytest.mark.parametrize(
        "left,right",
        [
            ("2025.1.1", "2026.08.19"),
            ("2025.9.30", "2025.10.1"),
            ("1.0", "1.0.1"),
            ("0", "1"),
        ],
    )
    def test_naive_version_ordering(self, left, right):
        assert _naive_version(left) < _naive_version(right)

    def test_naive_version_of_garbage_is_zero(self):
        assert _naive_version("rc-preview") == (0,)

    def test_yt_dlp_version_is_real(self):
        import yt_dlp.version

        version = yt_dlp_version()
        assert version == yt_dlp.version.__version__
        assert "unavailable" not in version


class TestInstallHint:
    def test_hint_mentions_ffmpeg_and_platform_package_manager(self):
        hint = ffmpeg_install_hint()
        assert "ffmpeg" in hint.lower()
        if shutil.which("brew"):
            assert "brew install ffmpeg" in hint
