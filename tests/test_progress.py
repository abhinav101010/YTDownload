"""Download state machine, progress parsing and formatting helpers."""

from __future__ import annotations

import pytest

from downloader.progress import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    DownloadStatus,
    InvalidTransition,
    ProgressInfo,
    TaskSnapshot,
    can_transition,
    format_bytes,
    format_duration,
    format_eta,
    format_speed,
    parse_postprocessor,
    parse_progress,
    statuses_in,
    transition,
)


class TestStateMachine:
    @pytest.mark.parametrize(
        "current,target",
        [
            (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING),
            (DownloadStatus.QUEUED, DownloadStatus.CANCELLED),
            (DownloadStatus.QUEUED, DownloadStatus.FAILED),
            (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING),
            (DownloadStatus.DOWNLOADING, DownloadStatus.COMPLETED),
            (DownloadStatus.DOWNLOADING, DownloadStatus.FAILED),
            (DownloadStatus.DOWNLOADING, DownloadStatus.CANCELLED),
            (DownloadStatus.PROCESSING, DownloadStatus.COMPLETED),
            (DownloadStatus.PROCESSING, DownloadStatus.FAILED),
            (DownloadStatus.FAILED, DownloadStatus.QUEUED),      # retry
            (DownloadStatus.CANCELLED, DownloadStatus.QUEUED),   # retry
        ],
    )
    def test_legal_transitions(self, current, target):
        assert can_transition(current, target)
        assert transition(current, target) is target

    @pytest.mark.parametrize(
        "current,target",
        [
            (DownloadStatus.COMPLETED, DownloadStatus.QUEUED),
            (DownloadStatus.COMPLETED, DownloadStatus.DOWNLOADING),
            (DownloadStatus.DOWNLOADING, DownloadStatus.QUEUED),
            (DownloadStatus.QUEUED, DownloadStatus.COMPLETED),
            (DownloadStatus.QUEUED, DownloadStatus.PROCESSING),
            (DownloadStatus.FAILED, DownloadStatus.COMPLETED),
            (DownloadStatus.CANCELLED, DownloadStatus.DOWNLOADING),
            (DownloadStatus.COMPLETED, DownloadStatus.FAILED),
        ],
    )
    def test_illegal_transitions_raise(self, current, target):
        assert not can_transition(current, target)
        with pytest.raises(InvalidTransition) as excinfo:
            transition(current, target)
        assert excinfo.value.current is current
        assert excinfo.value.target is target
        assert current.value in str(excinfo.value)

    def test_terminal_and_active_sets_are_disjoint(self):
        assert not (TERMINAL_STATUSES & ACTIVE_STATUSES)
        assert set(DownloadStatus) == TERMINAL_STATUSES | ACTIVE_STATUSES

    def test_coerce(self):
        assert DownloadStatus.coerce("queued") is DownloadStatus.QUEUED
        assert DownloadStatus.coerce(" COMPLETED ") is DownloadStatus.COMPLETED
        assert DownloadStatus.coerce(DownloadStatus.FAILED) is DownloadStatus.FAILED
        assert DownloadStatus.coerce("bogus") is None
        assert DownloadStatus.coerce(None) is None

    def test_statuses_in(self):
        assert statuses_in(["queued", "nonsense", DownloadStatus.FAILED]) == {
            DownloadStatus.QUEUED,
            DownloadStatus.FAILED,
        }


class TestParseProgress:
    def test_full_payload(self):
        info = parse_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 500,
                "total_bytes": 1000,
                "speed": 250.0,
                "eta": 2,
            },
            stage="Downloading…",
        )
        assert info.status is DownloadStatus.DOWNLOADING
        assert info.percent == 50.0
        assert info.downloaded_bytes == 500
        assert info.total_bytes == 1000
        assert not info.total_is_estimate
        assert info.speed == 250.0
        assert info.eta == 2
        assert info.stage == "Downloading…"

    def test_unknown_total_yields_no_percent(self):
        info = parse_progress({"downloaded_bytes": 12345})
        assert info.percent is None
        assert info.total_bytes is None
        assert info.downloaded_bytes == 12345

    def test_estimate_is_flagged(self):
        info = parse_progress(
            {"downloaded_bytes": 100, "total_bytes_estimate": 4000}
        )
        assert info.total_bytes == 4000
        assert info.total_is_estimate is True
        assert info.percent == 2.5

    def test_percent_falls_back_to_formatted_string(self):
        info = parse_progress({"total_bytes": 1000, "_percent_str": " 42.5%"})
        assert info.percent == 42.5

    def test_percent_clamped(self):
        assert parse_progress(
            {"downloaded_bytes": 5000, "total_bytes": 1000}
        ).percent == 100.0

    def test_garbage_values_are_ignored(self):
        info = parse_progress(
            {
                "downloaded_bytes": "soon",
                "total_bytes": None,
                "speed": "fast",
                "eta": -5,
                "_percent_str": "n/a%",
            }
        )
        assert info.percent is None
        assert info.speed is None
        assert info.eta is None

    def test_non_dict_input(self):
        info = parse_progress(None)  # type: ignore[arg-type]
        assert info.status is DownloadStatus.DOWNLOADING
        assert info.percent is None


class TestParsePostprocessor:
    def test_merger_label(self):
        info = parse_postprocessor(
            {"status": "started", "postprocessor": "Merger"}
        )
        assert info.status is DownloadStatus.PROCESSING
        assert info.stage == "Merging audio and video…"

    def test_audio_conversion_label(self):
        info = parse_postprocessor({"postprocessor": "FFmpegExtractAudio"})
        assert info.stage == "Converting audio…"

    def test_unknown_postprocessor_gets_generic_label(self):
        info = parse_postprocessor({"postprocessor": "SomeFuturePP"})
        assert info.stage and "SomeFuture" in info.stage

    def test_missing_name(self):
        info = parse_postprocessor({})
        assert info.status is DownloadStatus.PROCESSING
        assert info.stage == "Processing…"


class TestFormatting:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, "—"),
            (-1, "—"),
            (0, "0 B"),
            (512, "512 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (5 * 1024**2, "5.0 MB"),
            (3 * 1024**3, "3.0 GB"),
        ],
    )
    def test_format_bytes(self, value, expected):
        assert format_bytes(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [(None, "—"), (0, "—"), (-5, "—"), (2048, "2.0 KB/s")],
    )
    def test_format_speed(self, value, expected):
        assert format_speed(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [(None, "—"), (-1, "—"), (0, "0:00"), (65, "1:05"), (3725, "1:02:05")],
    )
    def test_format_eta(self, value, expected):
        assert format_eta(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [(None, "—"), (0, "0:00"), (59, "0:59"), (60, "1:00"), (3601, "1:00:01")],
    )
    def test_format_duration(self, value, expected):
        assert format_duration(value) == expected

    def test_format_duration_bad_input(self):
        assert format_duration("soon") == "—"


class TestTaskSnapshot:
    def _snapshot(self, status: DownloadStatus) -> TaskSnapshot:
        return TaskSnapshot(
            id="t1",
            url="https://example.com/v/1",
            title="T",
            thumbnail_url=None,
            quality_label="Best",
            format_label="MP4",
            mode="video",
            destination="/tmp",
            status=status,
        )

    @pytest.mark.parametrize("status", list(TERMINAL_STATUSES))
    def test_is_terminal(self, status):
        assert self._snapshot(status).is_terminal
        assert not self._snapshot(status).is_active

    @pytest.mark.parametrize("status", list(ACTIVE_STATUSES))
    def test_is_active(self, status):
        assert self._snapshot(status).is_active
        assert not self._snapshot(status).is_terminal

    def test_progress_defaults(self):
        snapshot = self._snapshot(DownloadStatus.QUEUED)
        assert isinstance(snapshot.progress, ProgressInfo)
        assert snapshot.error is None
        assert snapshot.file_path is None
