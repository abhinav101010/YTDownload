# Architecture

This document explains how YT Downloader is put together and why the pieces are
shaped the way they are. The short version: a Tk UI owns all widgets, a thin
controller wires pages to services through a thread-safe event bus, services
orchestrate work, and the `downloader` package is pure logic with exactly one
place that talks to yt-dlp.

```
                    ┌───────────────────────────────────────────────┐
                    │                Tk main thread                 │
                    │                                               │
   main.py ────────►│  YTDownloaderApp (app/application.py)         │
   (argv, Tcl/Tk)   │   • navigation, settings, dialogs, toasts     │
                    │   • _poll_events(): drains the EventBus       │
                    │     every 90 ms and applies events to widgets │
                    │   • Home / Downloads / History / Settings     │
                    └───────▲───────────────────────────┬───────────┘
                            │ events (EventBus)         │ actions
                            │                           ▼
                    ┌───────┴───────────────┐   ┌───────────────────────┐
                    │  app/state.py         │   │ services/             │
                    │  Event, EventBus      │   │  DownloadService      │
                    │  AppSettings, events  │◄──│  TaskScheduler        │
                    └───────────────────────┘   │  media/file_service   │
                                                └───────┬───────────────┘
                                                        │ one call per run
                                                        ▼
                                                ┌───────────────────────┐
                                                │ downloader/           │
                                                │  engine.py (yt-dlp)   │
                                                │  metadata/formats/    │
                                                │  validators/progress  │
                                                └───────────────────────┘
                                                        │
                                                        ▼
                                                ┌───────────────────────┐
                                                │ database/  (SQLite)   │
                                                └───────────────────────┘
```

## Layers

| Layer | Modules | Rules |
| --- | --- | --- |
| Presentation | `ui/`, `app/application.py` | Only this layer touches widgets, and only from the Tk main thread. |
| Orchestration | `services/` | No widgets. May spawn worker threads. Talks to the UI exclusively via `EventBus`. |
| Domain | `downloader/` | Pure functions + one engine class. No threads, no widgets, no DB. |
| Persistence | `database/` | Thin SQLite wrapper, repositories, no business logic. |
| Shared | `app/state.py`, `app/config.py` | Immutable settings, events, paths, defaults. |

Dependencies point downwards only: `ui → services → downloader/database`.
`downloader` never imports `ui`, `services` or `database`.

## Threading model

Tk is not thread-safe, so the rule is absolute: **widgets are only ever touched
on the main thread**. Background work communicates through `EventBus`:

1. A worker thread calls `bus.post(kind, payload)` — `EventBus` is just a
   `queue.Queue` plus a `drain()` helper, so posting is safe from anywhere.
2. The controller schedules `_poll_events()` with `root.after(90, …)`. Each tick
   drains the queue and dispatches events; `EVENT_DOWNLOAD` events update the
   matching row, `EVENT_METADATA` updates Home, `EVENT_THUMBNAIL` swaps in the
   image, `EVENT_TOAST` shows a toast, `EVENT_SYSTEM` handles ad-hoc results.
3. Every dispatch is wrapped so a single bad payload can never kill the pump;
   the next tick is always scheduled.

Threads that exist:

| Thread | Started by | Lifetime |
| --- | --- | --- |
| Tk main | `main.py` | whole run |
| Download worker | `DownloadService._launch` via `TaskScheduler.activate` | one download |
| Metadata fetch | `application.fetch_metadata` | one URL fetch |
| Thumbnail fetch | `application.request_thumbnail` | one image |
| Update check | `application._startup_update_check` | one HTTP call |

Because widgets are off-limits, worker code only ever reads immutable
snapshots: `TaskSnapshot` (frozen dataclass) is what crosses the boundary, so
the UI can never mutate engine state by accident.

### Event volume control

yt-dlp's progress hook fires very often. `DownloadService._post()` throttles
progress snapshots to one per ~150 ms, but **always** publishes when the
status changes, so the UI never misses a transition (`queued → downloading →
processing → completed/failed/cancelled`).

## Download lifecycle

```
submit() ──► QUEUED ──► DOWNLOADING ──► PROCESSING ──► COMPLETED
                │            │              │
                │            ├──────────────┴────────► FAILED
                └────────────┴───────────────────────► CANCELLED
                                  (retry: FAILED/CANCELLED ──► QUEUED)
```

`downloader/progress.py` encodes these transitions explicitly. Any other move
raises `InvalidTransition` instead of silently corrupting state — bugs surface
in tests rather than as a stuck row.

Per run, in order:

1. `submit()` rejects a duplicate URL that is still active, creates a task,
   enqueues it, publishes a `QUEUED` snapshot, and pumps the scheduler.
2. `TaskScheduler.pump()` starts as many tasks as the concurrency limit allows,
   calling `DownloadService._launch(task_id)`; the worker thread registers
   itself via `activate()` so the limit is enforced on real threads.
3. The worker transitions to `DOWNLOADING`, writes a `downloading` history row,
   then runs **preflight**: destination must be a writable folder, FFmpeg must
   exist if the chosen option needs it, and there must be enough free space.
   Preflight failures end the task as `FAILED` with an actionable message and
   never start yt-dlp.
4. `DownloadEngine.run()` performs the download and returns an `EngineResult`
   (it never raises). Errors are classified (`network`, `ffmpeg`, `download`,
   `processing`, `destination`, `internal`) and mapped to human text via
   `metadata.friendly_error`.
5. `_finish()` records the outcome, updates the history row, publishes a forced
   snapshot, releases the scheduler slot and pumps again.

### Cancellation

Cancellation is cooperative and honest:

- **Queued** tasks are removed from the queue and finish as `CANCELLED`
  immediately (no history row, because nothing ever ran).
- **Running** tasks get a `threading.Event`; yt-dlp's next hook raises
  `DownloadCancelled`, the engine cleans up *our* `.part` files and returns a
  cancelled result. Until that happens the row keeps showing `downloading` —
  the UI never claims a download stopped before it did.
- `shutdown()` (closing the app) cancels everything, joins worker threads with a
  timeout and reports how many were still alive; history rows left
  `downloading` become `interrupted` on the next launch (`recover_stale()`).

## Honest reporting rules

These are enforced in code, not just conventions:

- **Progress**: `percent` is only set when the total size is known. Otherwise it
  is `None` and the UI shows an indeterminate bar with speed/ETA if available.
- **Sizes**: `filesize` is exact; `filesize_approx` or bitrate×duration products
  are flagged `size_is_estimate` and rendered with `(est.)`.
- **Formats**: options are derived from the metadata's format table. If separate
  video/audio streams exist the "best" option is a merge and is marked as
  requiring FFmpeg; if only combined streams exist no merge is offered.
- **Post-processing ≠ completion**: a finished download hook does not mark the
  task complete while post-processors (merge/convert) are still pending.
- **Files**: `overwrites: False`, so an existing file is never clobbered.
- **Thumbnails**: a failed fetch shows a placeholder; the item is not hidden.

## URL safety

`downloader/validators.py` is the trust boundary. Before anything reaches
yt-dlp:

- only `http`/`https` are accepted (`file:`, `ftp:`, `javascript:`, `data:`,
  `magnet:` are rejected with specific messages);
- control characters, interior spaces, embedded credentials and over-long URLs
  are rejected;
- fragments are dropped; the query string is preserved (signed media URLs need
  it) but `redact_url()` strips it from log lines so tokens never land in
  `app.log`;
- YouTube channel/playlist URLs are detected so the user is told they're not a
  single video.

Filesystem input goes through `services/file_service.py`: `sanitize_filename()`
(strips path separators, control characters, bidi overrides, Windows-reserved
names, over-long stems, while keeping the extension), `validate_destination()`
and `unique_path()`.

## Database

SQLite in WAL mode, one connection guarded by an `RLock` (so worker threads and
the UI can both write safely), `PRAGMA user_version` for forward-only
migrations. Schema v1:

```
history(id, source_url, title, thumbnail_url, quality, output_format,
        destination_path, status, created_at, completed_at, error_message)
        + indexes on created_at DESC and status
settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)
```

Design decisions:

- **One history row per run.** Retrying opens a new row rather than mutating
  history, so the record shows what actually happened. (The *download list* row
  is reused, keeping its position in the UI.)
- **History failures never kill a download.** All repository calls in the
  download path are wrapped; a database problem degrades to "no history", logs
  a warning and lets the download finish.
- **Settings are key/value text, sanitised on both save and load** via
  `app/config.sanitize_settings()`, so a hand-edited or corrupted database can
  never inject invalid enums, non-numeric concurrency or an empty download
  folder. Unknown keys are dropped.

## UI structure

`ui/components.py` provides the reusable shell (sidebar, page header, cards,
badges, toasts, buttons); each page module owns its widget tree and exposes
imperative methods (`show_metadata()`, `apply_snapshot()`, `reload()`…). Pages
never call services directly for stateful work — they call the controller
(`app/application.py`), which services call back through events. This keeps a
single direction of data flow and makes pages easy to drive in tests
(`tools/ui_exercise.py` renders every page and every status offline).

Appearance: `ui/theme.py` holds a two-tone palette per colour (dark/light) and
a `font()` helper that creates `CTkFont` lazily (fonts need a live root).
Icons are pre-generated PNGs in `assets/icons/` (light + dark ink variants,
produced by `tools/generate_icons.py`) and cached by `ui/icons.py`.

## Testing strategy

| Level | Location | Network | What it covers |
| --- | --- | --- | --- |
| Unit | `tests/test_*.py` | no | validators, progress/state machine, formats, engine options + engine run with a stubbed `YoutubeDL`, file service, queue scheduler, download service with a stubbed engine, database/repositories/settings, config |
| UI driver | `tools/ui_exercise.py` | no | builds the app and drives every page/status/action offline |
| Smoke | `main.py --smoke-test` | no | full startup, render and clean shutdown |
| Integration | `tests/integration/` (opt-in, `-m integration`) | yes | real metadata fetch and a real audio download |

Tests never touch real user data: the root `conftest.py` sets
`YTDL_APP_DATA_DIR` to a temporary folder before any application module is
imported. Background behaviour is tested deterministically with engine stubs
that block until a test signals them, so cancellation, retry and concurrency
limits are exercised without sleeping races.

## Extension points

- **New quality/format rules** → `downloader/formats.py` (keep it derived from
  metadata; never hard-code resolutions).
- **New site support** → nothing to do, it comes with yt-dlp; adjust
  `validators.py` only if you want special URL handling.
- **New persisted setting** → add it to `DEFAULT_SETTINGS`, enums in
  `config.py`, the sanitizer, `AppSettings`, and a control in `settings_page.py`.
- **New schema** → bump `SCHEMA_VERSION` and add an `elif version < N` branch in
  `Database._migrate()`; never edit v1 in place.
- **New event type** → add a constant in `app/state.py` and a branch in
  `YTDownloaderApp._poll_events()`.
