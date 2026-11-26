"""Format selection: options must reflect what the source actually offers."""

from __future__ import annotations

import pytest

from downloader.formats import (
    build_audio_options,
    build_video_options,
    find_option,
    option_detail,
    pick_option,
)

# A YouTube-like format table: separate video-only + audio-only streams
# (requires merging) plus a low combined format.
YOUTUBE_LIKE = [
    {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none",
     "height": 1080, "tbr": 4500, "filesize": 70_000_000, "ext": "mp4"},
    {"format_id": "248", "vcodec": "vp9", "acodec": "none",
     "height": 1080, "tbr": 4200, "ext": "webm", "filesize_approx": 66_000_000},
    {"format_id": "136", "vcodec": "avc1.4d401f", "acodec": "none",
     "height": 720, "tbr": 2500, "filesize": 40_000_000, "ext": "mp4"},
    {"format_id": "18", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2",
     "height": 360, "tbr": 600, "filesize": 9_000_000, "ext": "mp4"},
    {"format_id": "251", "vcodec": "none", "acodec": "opus",
     "abr": 160, "ext": "webm", "filesize": 12_000_000},
    {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2",
     "abr": 128, "ext": "m4a", "filesize": 10_000_000},
]

COMBINED_ONLY = [
    {"format_id": "22", "vcodec": "avc1", "acodec": "mp4a.40.2",
     "height": 720, "tbr": 2000, "filesize": 30_000_000, "ext": "mp4"},
    {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a.40.2",
     "height": 360, "tbr": 600, "ext": "mp4", "filesize": 9_000_000},
]


class TestVideoOptions:
    def test_best_option_merges_when_separate_streams_exist(self):
        best = build_video_options(YOUTUBE_LIKE, duration=600)[0]
        assert best.key == "best"
        assert best.requires_merge is True
        assert best.requires_ffmpeg is True
        assert best.format_selector == "bestvideo+bestaudio/best"

    def test_heights_are_unique_and_sorted_best_first(self):
        options = build_video_options(YOUTUBE_LIKE, duration=600)
        heights = [o.height for o in options[1:]]
        assert heights == sorted(set(heights), reverse=True)
        assert heights == [1080, 720, 360]
        labels = [o.label for o in options]
        assert len(labels) == len(set(labels))

    def test_height_option_selects_matching_height(self):
        options = build_video_options(YOUTUBE_LIKE, duration=600)
        option_720 = find_option(options, "720p")
        assert option_720 is not None
        assert "height=720" in option_720.format_selector
        assert option_720.requires_ffmpeg is True  # video-only stream

    def test_combined_only_needs_no_ffmpeg(self):
        options = build_video_options(COMBINED_ONLY, duration=600)
        best = options[0]
        assert best.requires_ffmpeg is False
        assert best.requires_merge is False
        option_720 = find_option(options, "720p")
        assert option_720.format_selector == "best[height=720]"
        assert option_720.requires_ffmpeg is False

    def test_exact_size_is_not_an_estimate(self):
        options = build_video_options(YOUTUBE_LIKE, duration=600)
        option_1080 = find_option(options, "1080p")
        # 70 MB video + 12 MB audio (best audio) = 82 MB, exact.
        assert option_1080.size_bytes == 70_000_000 + 12_000_000
        assert option_1080.size_is_estimate is False

    def test_approx_size_is_flagged_as_estimate(self):
        options = build_video_options(
            [YOUTUBE_LIKE[1], YOUTUBE_LIKE[4]], duration=600
        )
        option_1080 = find_option(options, "1080p")
        assert option_1080.size_is_estimate is True

    def test_size_estimated_from_bitrate_when_no_size_reported(self):
        formats = [
            {"format_id": "v", "vcodec": "vp9", "acodec": "none",
             "height": 1440, "tbr": 4000},
            {"format_id": "a", "vcodec": "none", "acodec": "opus", "abr": 128},
        ]
        option_1440 = find_option(build_video_options(formats, duration=120), "1440p")
        assert option_1440.size_bytes is not None
        assert option_1440.size_is_estimate is True
        # 4000 kbit/s * 120 s / 8 = 60 MB (+ audio est.)
        assert option_1440.size_bytes >= 60_000_000

    def test_empty_format_list_gives_single_fallback(self):
        options = build_video_options([], duration=None)
        assert len(options) == 1
        assert options[0].key == "best"
        assert options[0].format_selector == "bestvideo+bestaudio/best"

    def test_garbage_entries_are_ignored(self):
        options = build_video_options(
            ["not a dict", None, 42, {"format_id": "x"}], duration=10
        )
        assert options and options[0].key == "best"

    def test_container_is_propagated(self):
        options = build_video_options(YOUTUBE_LIKE, container="mkv")
        assert all(o.container == "mkv" for o in options)


class TestAudioOptions:
    def test_mp3_option_first_and_requires_ffmpeg(self):
        options = build_audio_options(YOUTUBE_LIKE, duration=600)
        assert options[0].key == "audio:mp3"
        assert options[0].convert_audio is True
        assert options[0].requires_ffmpeg is True
        assert options[0].audio_ext == "mp3"

    def test_source_audio_streams_ranked_by_bitrate(self):
        options = build_audio_options(YOUTUBE_LIKE, duration=600)
        source_keys = [o.key for o in options if o.key != "audio:mp3"]
        assert source_keys[0] == "audio:webm"  # opus 160 kbps ranks first
        assert "audio:m4a" in source_keys
        for option in options:
            if option.key != "audio:mp3":
                assert option.requires_ffmpeg is False

    def test_mp3_can_be_hidden(self):
        options = build_audio_options(YOUTUBE_LIKE, allow_mp3=False)
        assert all(o.key != "audio:mp3" for o in options)

    def test_falls_back_to_combined_audio_track(self):
        options = build_audio_options(COMBINED_ONLY, duration=600)
        keys = [o.key for o in options]
        assert "audio:best" in keys
        best = find_option(options, "audio:best")
        assert best.requires_ffmpeg is False

    def test_no_formats_at_all_still_offers_sensible_defaults(self):
        # Without metadata there is nothing honest to rank, but the two
        # fallbacks (converted MP3, or the source's original audio track)
        # remain available.
        options = build_audio_options([], duration=None)
        assert [o.key for o in options] == ["audio:mp3", "audio:best"]
        assert options[0].requires_ffmpeg is True   # MP3 needs conversion
        assert options[1].requires_ffmpeg is False  # stream copy

    def test_labels_include_bitrate(self):
        options = build_audio_options(YOUTUBE_LIKE, duration=600)
        m4a = find_option(options, "audio:m4a")
        assert "128 kbps" in m4a.label


class TestSelectionHelpers:
    def test_find_option(self):
        options = build_video_options(YOUTUBE_LIKE, duration=600)
        assert find_option(options, "720p") is not None
        assert find_option(options, "4320p") is None
        assert find_option(options, None) is None
        assert find_option([], "x") is None

    def test_pick_option_prefers_matching(self):
        options = build_video_options(YOUTUBE_LIKE, duration=600)
        option, matched = pick_option(options, "720p")
        assert option.key == "720p"
        assert matched is True

    def test_pick_option_falls_back_to_first(self):
        options = build_video_options(YOUTUBE_LIKE, duration=600)
        option, matched = pick_option(options, "audio:mp3")  # wrong mode's key
        assert option is options[0]
        assert matched is False

    def test_pick_option_empty(self):
        assert pick_option([], "best") == (None, False)

    def test_option_detail_includes_size_container_and_note(self):
        options = build_video_options(YOUTUBE_LIKE, duration=600)
        detail = option_detail(find_option(options, "1080p"))
        assert "MB" in detail
        assert "→ MP4" in detail
        assert "FFmpeg" in detail

    def test_option_detail_none(self):
        assert option_detail(None) == ""

    def test_size_label_marks_estimates(self):
        options = build_video_options(COMBINED_ONLY, duration=600)
        assert "(est.)" not in find_option(options, "720p").size_label
        options_unknown = build_video_options(
            [{"format_id": "1", "vcodec": "avc1", "acodec": "mp4a", "height": 720}],
            duration=None,
        )
        assert find_option(options_unknown, "720p").size_label == ""
