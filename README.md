# YT Downloader

A local desktop app for downloading video and audio from URLs supported by
[yt-dlp](https://github.com/yt-dlp/yt-dlp) — with a real queue, real progress,
real cancellation and a real history database. No mock data, no simulated
progress: every number shown comes from yt-dlp.

Built with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter),
yt-dlp, Pillow and SQLite. Runs entirely on your machine; nothing is sent
anywhere except requests to the site you ask it to download from.

> **Use responsibly.** Only download content you have the right to download
> (your own uploads, public-domain/CC material, or content whose licence
> permits it). This app does not circumvent DRM, does not bypass access
> restrictions, and does not download private, members-only or age-restricted
> content — those cases surface as a clear error instead.

---

## Features

**Home — pick exactly what you get**

- Paste a URL, fetch real metadata (title, channel, duration, thumbnail).
- Quality options are built from the source's *actual* format list — nothing is
  invented, and unavailable resolutions are never promised.
- Exact file sizes are shown as-is; estimated sizes are marked `(est.)`.
- Options that need FFmpeg (merging separate video+audio streams, MP3
  conversion) are labelled and disabled with an explanation when FFmpeg is
  missing.
- Audio mode with MP3 conversion or "keep the source audio track".
- Channels/playlists are detected and flagged before you queue them
  (per-video extraction is used, never a silent 500-item playlist).
- Live streams and unsupported links are rejected with a clear message.

**Downloads — a real, bounded queue**

- Concurrency limit (1–4) with FIFO ordering; the setting applies immediately.
- One live row per download with determinate progress when a total size is
  known and an indeterminate bar when it isn't.
- Honest cancellation: queued items cancel instantly, running items keep
  showing "downloading" until the worker has actually stopped.
- Retry, remove-from-list, open file, reveal in folder.
- Duplicate URLs are rejected while they're still active.

**History — SQLite-backed**

- Every run writes a row (a retry starts a new row; nothing is overwritten).
- Search across titles and URLs, filter by status.
- Rows tell you when the file is missing on disk (`stale`) instead of pretending.
- Delete a history entry without touching the file; clear all history.
- Rows left `downloading` by a crash are marked `interrupted` on the next launch.

**Settings**

- Download folder (validated: must exist, be a folder, be writable).
- Default mode, preferred quality, merge container (MP4/MKV/WebM).
- Maximum concurrent downloads.
- Appearance (Dark/Light/System) and accent colour, persisted across restarts.
- FFmpeg detection (configured path → `PATH`) with version, or install hints.
- yt-dlp version, "check now" against PyPI, and an optional startup check.
- Reset to defaults (never touches your files or history).

**Engineering details worth knowing**

- The Tk main thread owns every widget; workers communicate only through a
  thread-safe event bus drained by the UI (`after(90ms)`), so nothing touches
  Tk from a background thread.
- Progress events are throttled (~150 ms) while status changes are always sent.
- `overwrites: False` — an existing file is never clobbered; yt-dlp picks a
  free name instead.
- Partial files from our own downloads are cleaned up on cancel/failure, and
  cleanup only ever targets files carrying the video-id marker we wrote.

---

## Requirements

- Python 3.10+ (developed and tested on CPython 3.14, macOS arm64).
  The interpreter must include Tk (`_tkinter`).
- **FFmpeg is optional but recommended.** It's required to merge separate
  video+audio streams (most 1080p+ downloads) and to convert audio (e.g. MP3).
  Without it those specific options are disabled with an explanation — the app
  itself still works.
- Dependencies: `customtkinter`, `yt-dlp`, `pillow`, `darkdetect`, `packaging`
  (see `requirements.txt`), plus `pytest` for the test suite.

## Install

```bash
cd YTDownload
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

For tests and packaging, install the dev extras instead:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt   # pytest + pyinstaller
```

A `uv`-managed interpreter works too and generally has Tk available:

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
```

## Run

```bash
.venv/bin/python main.py            # normal launch
.venv/bin/python main.py --verbose  # detailed console logging
```

Optional flags:

| Flag | Purpose |
| --- | --- |
| `--verbose` | debug-level console logging |
| `--smoke-test` | build every page, pump the event loop briefly, print `SMOKE OK`, exit (used by CI) |

Environment variable:

| Variable | Purpose |
| --- | --- |
| `YTDL_APP_DATA_DIR` | override the app data folder (database, logs, thumbnail cache) — handy for portable installs and tests |

App data lives in `~/Library/Application Support/YTDownloader` on macOS
(`%LOCALAPPDATA%\YTDownloader` on Windows, `$XDG_DATA_HOME/YTDownloader` on Linux):
`yt_downloader.db`, `logs/app.log`, `cache/`.

## FFmpeg setup

The app looks for FFmpeg in this order: the path you configure in
**Settings → FFmpeg path**, then `PATH`. Settings shows exactly what was found.

**macOS (Homebrew)**

```bash
brew install ffmpeg
```

Then reopen the app (or just Settings) — the status line should read
`FFmpeg found — /opt/homebrew/bin/ffmpeg (8.x)`.

**Other options**

- Download a static build from <https://ffmpeg.org/download.html> and point
  **Settings → FFmpeg path** at the `ffmpeg` binary (or the folder containing it).
- Linux: `sudo apt install ffmpeg` (Debian/Ubuntu) or `sudo dnf install ffmpeg`.
- Windows: `winget install Gyan.FFmpeg`, or unpack a build and set the path.

If FFmpeg is missing, every download that needs it fails *before* starting with
an install hint, rather than halfway through.

## Tests

Unit tests are fully offline (the yt-dlp engine is stubbed) and never touch your
real data — they point `YTDL_APP_DATA_DIR` at a temp folder:

```bash
.venv/bin/python -m pytest              # 289 tests, no network
.venv/bin/python -m pytest -q -k formats
```

Two opt-in integration tests hit the real network (metadata fetch + a real
audio download of a 19-second public video):

```bash
.venv/bin/python -m pytest -m integration tests/integration -v
```

There is also a development tool that renders and drives every page offline
(needs a display, not part of pytest):

```bash
.venv/bin/python tools/ui_exercise.py
```

## Project layout

```
main.py                  entry point (argparse, Tcl/Tk fixups, error handling)
app/
  config.py              paths, defaults, settings sanitisation, logging
  state.py               AppSettings, Event, EventBus
  application.py         controller: wiring, navigation, event pump, actions
downloader/              no UI, no threads of its own
  validators.py          URL validation, redaction, playlist/channel detection
  metadata.py            metadata fetch, thumbnails, friendly error mapping
  formats.py             metadata → honest quality options
  progress.py            state machine, progress parsing, formatting
  engine.py              yt-dlp options + the single download engine
services/                orchestration
  queue_service.py       FIFO scheduler with a concurrency limit
  download_service.py    submit/cancel/retry, preflight, history, events
  media_service.py       FFmpeg detection, yt-dlp version/update checks
  file_service.py        naming, destination validation, disk space, opening
database/                SQLite
  database.py            connection, migrations (PRAGMA user_version)
  models.py              row models, status sets, display helpers
  repositories.py        HistoryRepository, SettingsRepository
ui/
  theme.py               palette, status pills, lazy fonts
  icons.py               cached icon loading (light/dark variants)
  components.py          Card, Sidebar, PageHeader, StatusBadge, Toaster…
  dialogs.py             modal confirm/message/folder pickers
  home_page.py           URL → metadata → options → download
  downloads_page.py      live rows, per-status actions
  history_page.py        search, filter, open, delete
  settings_page.py       auto-saving sections
assets/icons/            42 generated PNGs (light + dark variants)
tools/                   generate_icons.py, ui_exercise.py (dev tools)
tests/                   pytest suite (+ opt-in integration tests)
docs/ARCHITECTURE.md     how it fits together and why
docs/PACKAGING.md        building a standalone .app/executable
```

## Packaging

See [docs/PACKAGING.md](docs/PACKAGING.md) for a ready-to-use PyInstaller spec
and step-by-step instructions for producing a standalone macOS `.app` (or a
Windows/Linux executable).

The spec in this repo has been used to build and run a macOS arm64 bundle
(`dist/YT Downloader.app`, ~44 MB with every yt-dlp extractor and Tcl/Tk
included). The bundled app starts, renders every page and passes
`--smoke-test`. One non-obvious pitfall is documented there: PyInstaller
silently drops `tkinter` unless `TCL_LIBRARY`/`TK_LIBRARY` are visible during
the build — the spec handles it.

## Known limitations

- No DRM/circumvention, no login/cookies support: private, members-only and
  age-restricted content fails with an explanatory error. Bot-verification
  pages ("Sign in to confirm you're not a bot") can't be solved by the app.
- Playlists and channels are intentionally treated as whole-item URLs to avoid
  surprise bulk downloads; use one video URL at a time.
- Progress can't be a percentage when the source doesn't report a total size —
  the UI shows an indeterminate bar rather than a fake number.
- Cancellation is cooperative: it stops as soon as yt-dlp's next hook fires, so
  a very large file already in flight stops within a moment, not instantly.
- Thumbnails are fetched from the source; if that request fails the app shows a
  placeholder instead.
- The queue is in-memory: closing the app cancels in-flight downloads (history
  rows for them are marked `interrupted` on the next launch).
- Downloads from sites that aggressively rate-limit or fingerprint clients may
  fail; update yt-dlp in Settings when a site changes.
