"""Engine option building and error classification (pure, offline)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app import config
from downloader.engine import (
    DownloadEngine,
    DownloadRequest,
    EngineErrorKind,
    build_ydl_opts,
)
from downloader.progress import DownloadStatus


def make_request(tmp_path: Path, **overrides) -> DownloadRequest:
    defaults = dict(
        url="https://example.com/watch?v=1",
        title="Sample",
        destination=Path(tmp_path),
        mode="video",
        format_selector="bestvideo+bestaudio/best",
        quality_label="Best",
        format_label="Merged → MP4",
        video_id="vid0001",
        container="mp4",
    )
    defaults.update(overrides)
    return DownloadRequest(**defaults)


class TestBuildYdlOpts:
    def test_common_options(self, tmp_path):
        opts = build_ydl_opts(make_request(tmp_path))
        assert opts["noplaylist"] is True
        assert opts["overwrites"] is False  # never clobber existing files
        assert opts["continuedl"] is True   # resume .part files
        assert opts["ignoreerrors"] is False  # errors must propagate
        assert opts["quiet"] is True
        assert opts["noprogress"] is True  # progress arrives via hooks only
        assert opts["socket_timeout"] == 20
        assert opts["retries"] >= 1
        assert "logger" in opts

    def test_outtmpl_is_inside_destination_with_id_marker(self, tmp_path):
        opts = build_ydl_opts(make_request(tmp_path))
        template = opts["outtmpl"]["default"]
        assert str(tmp_path) in template
        assert "[%(id)s]" in template, "id marker lets partial-cleanup target our files"
        assert "%(title)" in template

    def test_format_selector_passthrough_and_fallback(self, tmp_path):
        opts = build_ydl_opts(make_request(tmp_path, format_selector="best[height=720]"))
        assert opts["format"] == "best[height=720]"
        opts = build_ydl_opts(make_request(tmp_path, format_selector=""))
        assert opts["format"] == "best"

    def test_video_container_sets_merge_output_format(self, tmp_path):
        for container in ("mp4", "mkv", "webm"):
            opts = build_ydl_opts(make_request(tmp_path, container=container))
            assert opts["merge_output_format"] == container

    def test_audio_mode_without_container_has_no_merge(self, tmp_path):
        opts = build_ydl_opts(make_request(tmp_path, mode="audio", container=None))
        assert "merge_output_format" not in opts

    def test_audio_conversion_adds_ffmpeg_extract_audio(self, tmp_path):
        opts = build_ydl_opts(
            make_request(
                tmp_path,
                mode="audio",
                container=None,
                audio_ext="mp3",
                convert_audio=True,
            )
        )
        postprocessors = opts["postprocessors"]
        assert len(postprocessors) == 1
        pp = postprocessors[0]
        assert pp["key"] == "FFmpegExtractAudio"
        assert pp["format"] == "mp3"
        assert pp["preferredquality"] == "192"

    def test_audio_without_conversion_adds_no_postprocessors(self, tmp_path):
        opts = build_ydl_opts(
            make_request(tmp_path, mode="audio", container=None, convert_audio=False)
        )
        assert "postprocessors" not in opts

    def test_ffmpeg_location_only_when_configured(self, tmp_path):
        opts = build_ydl_opts(make_request(tmp_path))
        assert "ffmpeg_location" not in opts
        opts = build_ydl_opts(make_request(tmp_path, ffmpeg_path="/opt/bin/ffmpeg"))
        assert opts["ffmpeg_location"] == "/opt/bin/ffmpeg"

    def test_hooks_registered_when_provided(self, tmp_path):
        hook = lambda d: None  # noqa: E731
        opts = build_ydl_opts(make_request(tmp_path), progress_hook=hook)
        assert opts["progress_hooks"] == [hook]
        assert "postprocessor_hooks" not in opts

        opts = build_ydl_opts(
            make_request(tmp_path), progress_hook=hook, postprocessor_hook=hook
        )
        assert opts["postprocessor_hooks"] == [hook]

    def test_cache_lives_in_app_data_dir_not_download_dir(self, tmp_path):
        opts = build_ydl_opts(make_request(tmp_path))
        assert opts["cachedir"].startswith(str(config.app_data_dir()))
        assert str(tmp_path) not in opts["cachedir"]

    def test_thumbnail_and_subtitle_writing_disabled(self, tmp_path):
        opts = build_ydl_opts(make_request(tmp_path))
        assert opts["writethumbnail"] is False
        assert opts["writesubtitles"] is False
        assert opts["writeautomaticsub"] is False


class TestClassifyError:
    classify = staticmethod(DownloadEngine._classify_error)

    def test_ffmpeg_missing(self):
        assert (
            self.classify(RuntimeError("FFmpeg not found on this system"))
            == EngineErrorKind.FFMPEG
        )
        assert (
            self.classify(RuntimeError("ffmpeg is not installed"))
            == EngineErrorKind.FFMPEG
        )

    @pytest.mark.parametrize(
        "message",
        [
            "Connection timed out",
            "urlopen error: network unreachable",
            "getaddrinfo failed for host",
            "name resolution failed",
            "HTTP Error 503: temporary failure",
        ],
    )
    def test_network(self, message):
        assert self.classify(RuntimeError(message)) == EngineErrorKind.NETWORK

    def test_processing(self):
        assert (
            self.classify(RuntimeError("Postprocessing: something went wrong"))
            == EngineErrorKind.PROCESSING
        )

    @pytest.mark.parametrize(
        "message",
        ["Unsupported URL: https://x", "Video unavailable", "HTTP Error 404"],
    )
    def test_download_fallback(self, message):
        assert self.classify(RuntimeError(message)) == EngineErrorKind.DOWNLOAD

    def test_ffmpeg_beats_network_keyword_when_both_present(self):
        # "FFmpeg ... not found" must win over generic connection wording.
        message = "Post process failed: ffmpeg executable not found"
        assert self.classify(RuntimeError(message)) == EngineErrorKind.FFMPEG


class TestFriendlyErrors:
    def test_ffmpeg_kind_gets_install_hint(self):
        message = DownloadEngine._friendly(
            RuntimeError("ffmpeg not found"), EngineErrorKind.FFMPEG
        )
        assert "brew install ffmpeg" in message

    def test_network_error_is_human_readable(self):
        message = DownloadEngine._friendly(
            RuntimeError("urlopen error: connection timed out"),
            EngineErrorKind.NETWORK,
        )
        assert message
        assert "urlopen" not in message.lower()


class FakeYoutubeDL:
    """Stands in for ``yt_dlp.YoutubeDL`` to exercise run() offline."""

    def __init__(self, opts, *, info=None, error=None):
        self.opts = opts
        self.info = info
        self.error = error
        self.hook_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=True, process=True):
        for hook in self.opts.get("progress_hooks") or []:
            hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10})
            self.hook_calls.append("downloading")
            hook({"status": "finished"})
            self.hook_calls.append("finished")
        for hook in self.opts.get("postprocessor_hooks") or []:
            hook({"status": "started", "postprocessor": "Merger"})
            self.hook_calls.append("postprocessor")
        if self.error is not None:
            raise self.error
        return self.info

    def prepare_filename(self, info):  # pragma: no cover - fallback path only
        raise RuntimeError("not used in these tests")


class TestEngineRun:
    def _patch(self, monkeypatch, *, info=None, error=None) -> list:
        """Replace yt_dlp.YoutubeDL; returns the list of created fakes."""
        from downloader import engine as engine_module

        created: list[FakeYoutubeDL] = []

        def factory(opts):
            fake = FakeYoutubeDL(opts, info=info, error=error)
            created.append(fake)
            return fake

        monkeypatch.setattr(engine_module.yt_dlp, "YoutubeDL", factory)
        return created

    def test_success_without_optional_stage_callback(self, tmp_path, monkeypatch):
        output = tmp_path / "Sample [vid0001].m4a"
        output.write_bytes(b"\x00" * 64)
        created = self._patch(
            monkeypatch, info={"requested_downloads": [{"filepath": str(output)}]}
        )

        progress: list = []
        result = DownloadEngine().run(
            make_request(tmp_path, mode="audio", container=None, audio_ext="m4a"),
            threading.Event(),
            on_progress=progress.append,
            on_stage=None,  # must not crash on the "finished" hook
        )

        assert result.ok, result.error
        assert result.file_path == output
        assert progress, "progress callbacks should have fired"
        assert created[0].hook_calls == ["downloading", "finished", "postprocessor"]

    def test_stage_callback_receives_finalising_and_processing(self, tmp_path, monkeypatch):
        output = tmp_path / "out.m4a"
        output.write_bytes(b"x")
        self._patch(
            monkeypatch, info={"requested_downloads": [{"filepath": str(output)}]}
        )

        stages: list = []
        result = DownloadEngine().run(
            make_request(tmp_path, mode="audio", container=None, audio_ext="m4a"),
            threading.Event(),
            on_progress=lambda _info: None,
            on_stage=stages.append,
        )
        assert result.ok
        stage_names = [info.stage for info in stages]
        assert any("finalizing" in (name or "").lower() for name in stage_names)
        assert any(info.status is DownloadStatus.PROCESSING for info in stages)

    def test_cancellation_returns_cancelled_result(self, tmp_path, monkeypatch):
        output = tmp_path / "out.m4a"
        output.write_bytes(b"x")
        self._patch(
            monkeypatch, info={"requested_downloads": [{"filepath": str(output)}]}
        )

        cancel = threading.Event()
        cancel.set()  # cancelled before any hook runs

        result = DownloadEngine().run(
            make_request(tmp_path, mode="audio", container=None, audio_ext="m4a"),
            cancel,
            on_progress=lambda _info: None,
            on_stage=lambda _info: None,
        )
        assert result.ok is False
        assert result.cancelled is True
        assert result.error_kind == EngineErrorKind.CANCELLED

    def test_download_error_is_classified_and_friendly(self, tmp_path, monkeypatch):
        from yt_dlp.utils import DownloadError

        self._patch(monkeypatch, error=DownloadError("HTTP Error 404: Not Found"))

        result = DownloadEngine().run(
            make_request(tmp_path),
            threading.Event(),
            on_progress=lambda _info: None,
        )
        assert result.ok is False
        assert result.cancelled is False
        assert result.error_kind == EngineErrorKind.DOWNLOAD
        assert result.error

    def test_missing_ffmpeg_error_gets_install_hint(self, tmp_path, monkeypatch):
        from yt_dlp.utils import DownloadError

        self._patch(monkeypatch, error=DownloadError("ffmpeg not found"))

        result = DownloadEngine().run(
            make_request(tmp_path),
            threading.Event(),
            on_progress=lambda _info: None,
        )
        assert result.error_kind == EngineErrorKind.FFMPEG
        assert "brew install ffmpeg" in result.error

    def test_filesystem_error_reported_as_destination_problem(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, error=OSError(28, "No space left on device"))

        result = DownloadEngine().run(
            make_request(tmp_path),
            threading.Event(),
            on_progress=lambda _info: None,
        )
        assert result.ok is False
        assert result.error_kind == EngineErrorKind.DESTINATION
        assert "space" in result.error.lower()

    def test_partial_files_cleaned_up_after_cancellation(self, tmp_path, monkeypatch):
        partial = tmp_path / "Sample [vid0001].m4a.part"
        partial.write_bytes(b"half")
        self._patch(monkeypatch, info=None)

        cancel = threading.Event()
        cancel.set()
        DownloadEngine().run(
            make_request(tmp_path, mode="audio", container=None, audio_ext="m4a"),
            cancel,
            on_progress=lambda _info: None,
        )
        assert not partial.exists(), "our own .part files must be cleaned up"
