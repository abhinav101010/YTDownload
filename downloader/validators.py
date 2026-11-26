"""URL validation and safe URL handling.

The application only ever downloads over ``http``/``https``.  Everything else
(file://, ftp://, javascript:, data:, embedded credentials, …) is rejected
before it reaches the download engine.  Metadata from remote sources is
treated as untrusted input; :func:`redact_url` keeps query strings (which may
carry tokens) out of log files.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from app.config import MAX_URL_LENGTH

ALLOWED_SCHEMES = {"http", "https"}

# Rejected explicitly so the error message can be specific and helpful.
_REJECTED_SCHEMES = {
    "file": "Local files cannot be opened with this app. Use a web URL.",
    "ftp": "FTP URLs are not supported.",
    "javascript": "Unsafe URL rejected.",
    "data": "Unsafe URL rejected.",
    "blob": "Blob URLs are not supported.",
    "magnet": "Magnet links are not supported.",
}

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

class InvalidURLError(ValueError):
    """Raised when a candidate URL is not an acceptable http(s) URL."""


def _contains_whitespace(value: str) -> bool:
    return any(ch.isspace() for ch in value)


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _looks_like_host(host: str) -> bool:
    if not host:
        return False
    if host.lower() in _LOCAL_HOSTS:
        return True
    if host.startswith("[") or ":" in host:  # IPv6 literal
        return True
    if host.replace(".", "").isdigit():  # IPv4 literal
        return True
    return "." in host


def normalize_url(url: str) -> str:
    """Return a stripped, canonicalized URL. Raises :class:`InvalidURLError`."""
    if url is None:
        raise InvalidURLError("Please enter a URL.")
    if not isinstance(url, str):
        raise InvalidURLError("Please enter a URL.")

    candidate = url.strip()
    if not candidate:
        raise InvalidURLError("Please enter a URL.")
    if len(candidate) > MAX_URL_LENGTH:
        raise InvalidURLError("That URL is too long to be valid.")
    if _has_control_chars(candidate):
        raise InvalidURLError("The URL contains invalid characters.")
    if _contains_whitespace(candidate):
        raise InvalidURLError("The URL must not contain spaces. Check what you pasted.")
    if "\\" in candidate:
        raise InvalidURLError("The URL contains invalid characters.")

    if "://" not in candidate:
        lowered = candidate.lower()
        for scheme, reason in _REJECTED_SCHEMES.items():
            if lowered.startswith(f"{scheme}:"):
                raise InvalidURLError(reason)
        raise InvalidURLError("The URL must start with http:// or https://.")

    try:
        parts = urlsplit(candidate)
    except ValueError as exc:  # malformed netloc/port
        raise InvalidURLError("That does not look like a valid URL.") from exc

    scheme = (parts.scheme or "").lower()
    if scheme in _REJECTED_SCHEMES:
        raise InvalidURLError(_REJECTED_SCHEMES[scheme])
    if scheme not in ALLOWED_SCHEMES:
        raise InvalidURLError("Only http:// and https:// URLs are supported.")

    if "@" in parts.netloc:
        raise InvalidURLError("URLs with embedded credentials are not accepted.")

    host = parts.hostname or ""
    if not _looks_like_host(host):
        raise InvalidURLError("That does not look like a valid URL (missing host name).")

    # Rebuild without the fragment: it never affects extraction and may
    # contain tracking tokens.
    normalized = urlunsplit(
        (scheme, parts.netloc, parts.path or "/", parts.query, "")
    )
    return normalized


def is_supported_url(url: str) -> bool:
    try:
        normalize_url(url)
    except InvalidURLError:
        return False
    return True


def is_youtube_url(url: str) -> bool:
    try:
        host = (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in _YOUTUBE_HOSTS


def is_whole_channel_or_playlist(url: str) -> bool:
    """True when the URL points at a channel/playlist instead of one video."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    if not path:
        return False
    if host in _YOUTUBE_HOSTS:
        if path == "/playlist":
            return True
        if path.startswith("/channel/") or path.startswith("/user/") or path.startswith("/c/"):
            return True
        if path.startswith("/@") or path.startswith("@"):  # /@handle
            return True
        return False
    return False


def redact_url(url: str) -> str:
    """Reduce a URL to scheme+host+path so query tokens never reach the log."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    if not parts.scheme:
        return "<no-scheme>"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
