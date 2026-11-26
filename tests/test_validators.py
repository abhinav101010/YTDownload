"""URL validation — the app's untrusted-input boundary. No network."""

from __future__ import annotations

import pytest

from app.config import MAX_URL_LENGTH
from downloader.validators import (
    InvalidURLError,
    is_supported_url,
    is_whole_channel_or_playlist,
    is_youtube_url,
    normalize_url,
    redact_url,
)


class TestNormalizeUrl:
    def test_strips_surrounding_whitespace(self):
        assert normalize_url("  https://example.com/watch?v=1 \n") == (
            "https://example.com/watch?v=1"
        )

    def test_adds_root_slash_for_bare_host(self):
        assert normalize_url("https://example.com") == "https://example.com/"

    def test_drops_fragment(self):
        assert normalize_url("https://example.com/a?x=1#tracking") == (
            "https://example.com/a?x=1"
        )

    @pytest.mark.parametrize("value", [None, "", "   ", "\t"])
    def test_empty_values_rejected(self, value):
        with pytest.raises(InvalidURLError, match="enter a URL"):
            normalize_url(value)

    def test_non_string_rejected(self):
        with pytest.raises(InvalidURLError):
            normalize_url(123)  # type: ignore[arg-type]

    def test_missing_scheme_rejected(self):
        with pytest.raises(InvalidURLError, match="http"):
            normalize_url("example.com/watch?v=1")

    @pytest.mark.parametrize(
        "url,needle",
        [
            ("ftp://example.com/file", "FTP"),
            ("file:///etc/passwd", "Local files"),
            ("javascript:alert(1)", "Unsafe"),
            ("data:text/html,<b>x</b>", "Unsafe"),
            ("magnet:?xt=abc", "Magnet"),
        ],
    )
    def test_rejected_schemes(self, url, needle):
        with pytest.raises(InvalidURLError, match=needle):
            normalize_url(url)

    def test_only_http_https_allowed(self):
        with pytest.raises(InvalidURLError, match="http"):
            normalize_url("gopher://example.com")

    def test_interior_spaces_rejected(self):
        with pytest.raises(InvalidURLError, match="spaces"):
            normalize_url("https://example.com/a b")

    def test_control_characters_rejected(self):
        with pytest.raises(InvalidURLError, match="invalid characters"):
            normalize_url("https://example.com/a\x00b")

    def test_backslash_rejected(self):
        with pytest.raises(InvalidURLError, match="invalid characters"):
            normalize_url("https://example.com\\evil")

    def test_embedded_credentials_rejected(self):
        with pytest.raises(InvalidURLError, match="credentials"):
            normalize_url("https://user:pass@example.com/x")

    def test_missing_host_rejected(self):
        with pytest.raises(InvalidURLError, match="host"):
            normalize_url("https://")

    def test_too_long_rejected(self):
        with pytest.raises(InvalidURLError, match="too long"):
            normalize_url("https://example.com/" + "a" * MAX_URL_LENGTH)

    def test_ipv6_literal_accepted(self):
        assert normalize_url("http://[::1]:8080/x") == "http://[::1]:8080/x"


class TestSupportPredicates:
    @pytest.mark.parametrize(
        "url,ok",
        [
            ("https://example.com/watch?v=1", True),
            ("https://youtu.be/abc", True),
            ("not a url", False),
            ("file:///tmp/x", False),
            ("", False),
        ],
    )
    def test_is_supported_url(self, url, ok):
        assert is_supported_url(url) is ok

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.youtube.com/watch?v=1", True),
            ("https://m.youtube.com/watch?v=1", True),
            ("https://music.youtube.com/watch?v=1", True),
            ("https://youtu.be/abc", True),
            ("https://youtube.com.evil.example/watch?v=1", False),
            ("https://vimeo.com/123", False),
            ("https://example.com/watch?v=1", False),
        ],
    )
    def test_is_youtube_url(self, url, expected):
        assert is_youtube_url(url) is expected

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.youtube.com/playlist?list=x", True),
            ("https://www.youtube.com/channel/UC123", True),
            ("https://www.youtube.com/@somehandle", True),
            ("https://www.youtube.com/user/somebody", True),
            ("https://www.youtube.com/watch?v=abc", False),
            ("https://www.youtube.com/", False),
            ("https://example.com/playlist", False),
        ],
    )
    def test_is_whole_channel_or_playlist(self, url, expected):
        assert is_whole_channel_or_playlist(url) is expected


class TestRedactUrl:
    def test_query_and_fragment_removed(self):
        redacted = redact_url("https://example.com/v/1?token=secret123#frag")
        assert redacted == "https://example.com/v/1"
        assert "secret123" not in redacted

    def test_keeps_scheme_host_path(self):
        assert redact_url("http://host:8080/path") == "http://host:8080/path"

    def test_unparseable_input(self):
        assert redact_url("::::") == "<no-scheme>"

    def test_scheme_only_input(self):
        # urlsplit without "//" treats everything as a path.
        assert redact_url("https://") in {"https://", "<no-scheme>"}
