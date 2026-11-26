"""Filesystem helpers: naming, destination validation, space, partial cleanup."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.file_service import (
    DestinationError,
    FileActionError,
    InsufficientSpaceError,
    cleanup_partial_files,
    ensure_free_space,
    existing_files,
    open_containing_folder,
    open_file,
    sanitize_filename,
    unique_path,
    validate_destination,
)


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("normal name.mp4", "normal name.mp4"),
            ("path/separator", "path_separator"),
            ("colon:star*question?", "colon_star_question_"),
            ("angle<brackets>|pipe", "angle_brackets__pipe"),
            ("quotes\"in\"name", "quotes_in_name"),
            ("back\\slash", "back_slash"),
            (None, "untitled"),
            ("", "untitled"),
            ("   ", "untitled"),
            ("...", "untitled"),
        ],
    )
    def test_basic_sanitising(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_control_characters_removed(self):
        assert sanitize_filename("a\x00b\x1fc") == "abc"

    def test_bidi_overrides_removed(self):
        evil = "invoice\u202eTXT.exe"
        assert "\u202e" not in sanitize_filename(evil)

    def test_whitespace_collapsed_and_trimmed(self):
        assert sanitize_filename("  a \t b   c  ") == "a b c"

    def test_windows_reserved_names_prefixed(self):
        assert sanitize_filename("CON.mp4").startswith("_")
        assert sanitize_filename("lpt1").startswith("_")

    def test_trailing_dots_stripped(self):
        assert sanitize_filename("name...") == "name"

    def test_long_name_keeps_extension(self):
        long_stem = "x" * 400
        result = sanitize_filename(f"{long_stem}.mp4")
        assert len(result) <= 180
        assert result.endswith(".mp4")

    def test_long_name_without_extension_truncated(self):
        assert len(sanitize_filename("y" * 400)) <= 180

    def test_custom_max_length(self):
        assert len(sanitize_filename("z" * 100, max_length=10)) <= 10


class TestValidateDestination:
    def test_none_rejected(self):
        with pytest.raises(DestinationError, match="No download folder"):
            validate_destination(None)

    def test_blank_rejected(self):
        with pytest.raises(DestinationError, match="No download folder"):
            validate_destination("   ")

    def test_relative_path_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(DestinationError, match="absolute"):
            validate_destination("relative/folder")

    def test_existing_file_rejected(self, tmp_path):
        target = tmp_path / "a-file"
        target.write_bytes(b"x")
        with pytest.raises(DestinationError, match="not a folder"):
            validate_destination(target)

    def test_missing_folder_without_create_rejected(self, tmp_path):
        with pytest.raises(DestinationError, match="does not exist"):
            validate_destination(tmp_path / "nope")

    def test_missing_folder_created_when_requested(self, tmp_path):
        target = tmp_path / "deep" / "nested"
        result = validate_destination(target, create=True)
        assert result == target
        assert target.is_dir()

    def test_existing_writable_folder_accepted(self, tmp_path):
        assert validate_destination(tmp_path) == tmp_path

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignores permission bits",
    )
    def test_unwritable_folder_rejected(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o555)
        try:
            with pytest.raises(DestinationError, match="not writable"):
                validate_destination(locked)
        finally:
            locked.chmod(0o755)


class TestEnsureFreeSpace:
    def test_unknown_size_passes(self, tmp_path):
        ensure_free_space(tmp_path, None)
        ensure_free_space(tmp_path, 0)
        ensure_free_space(tmp_path, -5)

    def test_small_requirement_passes(self, tmp_path):
        ensure_free_space(tmp_path, 1024)

    def test_impossible_requirement_raises(self, tmp_path):
        with pytest.raises(InsufficientSpaceError) as excinfo:
            ensure_free_space(tmp_path, 10**15)
        error = excinfo.value
        assert error.required_bytes > error.free_bytes
        assert "space" in str(error).lower()


class TestUniquePath:
    def test_free_path_returned_as_is(self, tmp_path):
        target = tmp_path / "video.mp4"
        assert unique_path(target) == target

    def test_existing_path_gets_counter(self, tmp_path):
        target = tmp_path / "video.mp4"
        target.write_bytes(b"1")
        first = unique_path(target)
        assert first.name == "video (1).mp4"
        first.write_bytes(b"2")
        assert unique_path(target).name == "video (2).mp4"


class TestCleanupPartialFiles:
    def test_only_marked_partials_are_removed(self, tmp_path):
        marked_part = tmp_path / "Title [vid123].mp4.part"
        marked_ytdl = tmp_path / "Title [vid123].mp4.ytdl"
        finished = tmp_path / "Title [vid123].mp4"
        other_part = tmp_path / "Unrelated download.mp4.part"
        user_file = tmp_path / "my own file.mp4"
        for path in (marked_part, marked_ytdl, finished, other_part, user_file):
            path.write_bytes(b"x")

        removed = cleanup_partial_files(tmp_path, "vid123")

        assert set(removed) == {marked_part, marked_ytdl}
        assert not marked_part.exists()
        assert not marked_ytdl.exists()
        assert finished.is_file()
        assert other_part.is_file()
        assert user_file.is_file()

    def test_without_video_id_nothing_is_touched(self, tmp_path):
        candidate = tmp_path / "Title [vid].mp4.part"
        candidate.write_bytes(b"x")
        assert cleanup_partial_files(tmp_path, None) == []
        assert candidate.is_file()

    def test_unknown_id_removes_nothing(self, tmp_path):
        candidate = tmp_path / "Title [other].mp4.part"
        candidate.write_bytes(b"x")
        assert cleanup_partial_files(tmp_path, "vid123") == []
        assert candidate.is_file()

    def test_missing_directory_is_fine(self, tmp_path):
        assert cleanup_partial_files(tmp_path / "gone", "vid") == []


class TestOpenHelpers:
    def test_open_file_without_path_raises(self):
        with pytest.raises(FileActionError, match="no file"):
            open_file(None)

    def test_open_missing_file_raises(self, tmp_path):
        with pytest.raises(FileActionError, match="no longer exists"):
            open_file(tmp_path / "gone.mp4")

    def test_reveal_without_path_raises(self):
        with pytest.raises(FileActionError, match="no folder"):
            open_containing_folder(None)

    def test_reveal_inside_missing_folder_raises(self, tmp_path):
        with pytest.raises(FileActionError, match="no longer exists"):
            open_containing_folder(tmp_path / "gone" / "file.mp4")

    def test_existing_files_filters(self, tmp_path):
        real = tmp_path / "real.mp4"
        real.write_bytes(b"x")
        results = existing_files([real, tmp_path / "missing.mp4", None, ""])
        assert results == [real]
