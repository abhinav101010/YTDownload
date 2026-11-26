"""YT Downloader application package.

Layers:

- ``app``        – bootstrap, configuration, shared state/event bus.
- ``downloader`` – pure download-domain logic (validation, metadata, formats,
                   progress, yt-dlp engine). No UI imports.
- ``services``   – orchestration (download queue, ffmpeg, filesystem).
- ``database``   – SQLite persistence.
- ``ui``         – CustomTkinter presentation layer.
"""

__version__ = "1.0.0"
