#!/usr/bin/env python3
"""UI exercise: construct every page and drive it through its data paths.

Development tool (not a pytest test — it needs a display). It renders the
whole interface offline: synthetic metadata for Home, a fake engine driving
the real queue/workers/history for Downloads, seeded rows for History, and
direct manipulation of Settings, dialogs and the event pump.

Run from the project root:

    .venv/bin/python tools/ui_exercise.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["YTDL_APP_DATA_DIR"] = tempfile.mkdtemp(prefix="ytdl-ui-exercise-")

from main import configure_tcl_tk  # noqa: E402

configure_tcl_tk()

# --- keep the exercise fully offline ---------------------------------------
from services import media_service  # noqa: E402

media_service.check_for_ytdlp_update = lambda: None

from app import config  # noqa: E402

config.setup_logging(verbose=False)

from app.application import YTDownloaderApp  # noqa: E402

import app.application as application_module  # noqa: E402

application_module.fetch_media_info = lambda _url: None
application_module.load_thumbnail = lambda _url: None

from database.models import now_iso  # noqa: E402
from downloader.engine import DownloadRequest, EngineResult  # noqa: E402
from downloader.metadata import MediaInfo  # noqa: E402
from downloader.progress import DownloadStatus, ProgressInfo, TaskSnapshot  # noqa: E402

failures: list[str] = []


def phase(name: str, fn) -> None:
    try:
        fn()
        app.update_idletasks()
    except Exception:
        failures.append(f"{name}: {traceback.format_exc()}")
        print(f"  FAIL  {name}")
    else:
        print(f"  ok    {name}")


def pump_events() -> None:
    for event in app.event_bus.drain(64):
        app._dispatch(event)


def wait_for(predicate, timeout: float = 6.0) -> bool:
    """Drive the event pump until ``predicate`` holds (or time out)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump_events()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def status_of(task_id: str) -> DownloadStatus | None:
    snapshot = app.download_service.get(task_id)
    return snapshot.status if snapshot else None


print("Building application…")
app = YTDownloaderApp()
print("  ok    construct")

DEST = Path(tempfile.mkdtemp(prefix="ytdl-dest-"))


def make_request(title: str, *, requires_ffmpeg: bool = False) -> DownloadRequest:
    return DownloadRequest(
        url=f"https://example.com/watch?v={abs(hash(title)) % 10**8}",
        title=title,
        destination=DEST,
        mode="video",
        format_selector="bestvideo+bestaudio/best",
        quality_label="1080p",
        format_label="Merged → MP4",
        video_id="vid0001",
        container="mp4",
        requires_ffmpeg=requires_ffmpeg,
    )


def fake_engine_run(request, cancel_event, on_progress, on_stage=None):
    """Offline stand-in for DownloadEngine.run with three behaviours."""
    if "Blocker" in request.title:
        # Runs until cancelled — exercises real cancellation plumbing.
        while not cancel_event.wait(0.05):
            pass
        return EngineResult(ok=False, cancelled=True, error="Cancelled by user")
    if "Doomed" in request.title:
        on_progress(
            ProgressInfo(status=DownloadStatus.DOWNLOADING, percent=55.0,
                         downloaded_bytes=550, total_bytes=1000, speed=100.0, eta=4)
        )
        return EngineResult(ok=False, error="Simulated download failure",
                            error_kind="download")
    on_progress(
        ProgressInfo(status=DownloadStatus.DOWNLOADING, percent=50.0,
                     downloaded_bytes=500, total_bytes=1000, speed=100.0, eta=5)
    )
    on_stage(ProgressInfo(status=DownloadStatus.PROCESSING, stage="Merging streams"))
    if cancel_event.is_set():
        return EngineResult(ok=False, cancelled=True, error="Cancelled by user")
    fake_file = DEST / "exercise-output.mp4"
    fake_file.write_bytes(b"\x00" * 32)
    return EngineResult(ok=True, file_path=fake_file)


app.download_service._engine.run = fake_engine_run  # type: ignore[method-assign]

# --------------------------------------------------------------------------- #
# Home
# --------------------------------------------------------------------------- #

sample_formats = [
    {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 160, "ext": "webm",
     "filesize": 12_000_000, "tbr": 160},
    {"format_id": "137+140", "vcodec": "avc1", "acodec": "none", "height": 1080,
     "tbr": 4500, "ext": "mp4", "filesize": 70_000_000, "protocol": "https"},
    {"format_id": "136+140", "vcodec": "avc1", "acodec": "none", "height": 720,
     "tbr": 2800, "ext": "mp4", "filesize": 44_000_000, "protocol": "https"},
    {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "height": 360,
     "tbr": 600, "ext": "mp4", "filesize": 9_000_000},
]
sample_info = MediaInfo(
    url="https://example.com/watch?v=abc123",
    video_id="abc123",
    title="A deliberately very long example video title that should wrap across "
          "several lines without breaking the layout of the card",
    uploader="Example Channel",
    duration=754,
    thumbnail_url=None,
    formats=sample_formats,
    webpage_url="https://example.com/watch?v=abc123",
    extractor="Example",
)


def home_flows() -> None:
    app.show("home")
    app.home.set_loading(True)
    app.home.show_url_error("That link isn't supported yet.")
    app.home.set_loading(False)
    app.home.show_metadata(sample_info)

    assert app.home._video_options, "video options should be built"
    assert app.home._audio_options, "audio options should be built"
    app.home.quality_menu.set(app.home._video_options[0].label)
    app.home._on_quality_change(app.home.quality_menu.get())
    request = app.home._build_request(app.home._video_options[0], app.ffmpeg_info)
    assert request is not None and request.video_id == "abc123"
    assert request.destination == DEST or request.destination.exists()

    app.home.mode_switch.set("Audio")
    app.home._on_mode_change("Audio")
    app.home.quality_menu.set(app.home._audio_options[0].label)
    app.home._on_quality_change(app.home._audio_options[0].label)

    app.home.mode_switch.set("Video")
    app.home._on_mode_change("Video")

    # Live stream: metadata renders but download is disabled.
    live_info = MediaInfo(
        url="https://example.com/watch?v=live", video_id="live", title="Live now",
        uploader="Channel", duration=None, thumbnail_url=None,
        formats=sample_formats, webpage_url="https://example.com/watch?v=live",
        extractor="Example", is_live=True,
    )
    app.home.show_metadata(live_info)
    assert str(app.home.download_button.cget("state")) == "disabled"
    app.home.clear_all()


phase("home flows", home_flows)

# --------------------------------------------------------------------------- #
# Downloads: pure row rendering for every status
# --------------------------------------------------------------------------- #

STATUSES = ["queued", "downloading", "processing", "completed", "failed", "cancelled"]


def render_every_status() -> None:
    app.show("downloads")
    for i, status_name in enumerate(STATUSES):
        status = DownloadStatus(status_name)
        percent = {"downloading": 42.7, "failed": 61.0}.get(status_name)
        snapshot = TaskSnapshot(
            id=f"render{i}",
            url=f"https://example.com/watch?v=r{i}",
            title=f"Render {status_name} item",
            thumbnail_url=None,
            quality_label="1080p",
            format_label="Merged → MP4",
            mode="video",
            destination=str(DEST),
            status=status,
            progress=ProgressInfo(
                status=status,
                percent=percent,
                downloaded_bytes=10_500_000 if percent else None,
                total_bytes=24_000_000,
                total_is_estimate=status_name == "downloading",
                speed=1_500_000.0,
                eta=9,
                stage="Merging streams" if status_name == "processing" else None,
            ),
            error="Simulated failure" if status_name == "failed" else None,
            file_path=str(DEST / "sample.mp4") if status_name == "completed" else None,
            created_at=now_iso(),
        )
        app.downloads.apply_snapshot(snapshot)

    # Button availability per status.
    rows = app.downloads.rows
    assert str(rows["render0"].buttons["cancel"].cget("state")) == "normal"    # queued
    assert str(rows["render0"].buttons["retry"].cget("state")) == "disabled"
    assert str(rows["render4"].buttons["retry"].cget("state")) == "normal"     # failed
    assert str(rows["render3"].buttons["open_file"].cget("state")) == "normal"  # completed
    assert str(rows["render2"].buttons["cancel"].cget("state")) == "normal"    # processing
    assert str(rows["render3"].buttons["remove"].cget("state")) == "normal"
    assert str(rows["render1"].buttons["remove"].cget("state")) == "disabled"  # active

    # Live updates: determinate -> indeterminate -> terminal.
    row = rows["render1"]
    base = dict(id="render1", url="https://example.com/watch?v=r1",
                title="Render downloading item", thumbnail_url=None,
                quality_label="1080p", format_label="Merged → MP4", mode="video",
                destination=str(DEST), created_at=now_iso())
    row.update(TaskSnapshot(
        **base, status=DownloadStatus.DOWNLOADING,
        progress=ProgressInfo(status=DownloadStatus.DOWNLOADING, percent=None,
                              speed=800_000.0, stage="Starting…"),
    ))
    row.update(TaskSnapshot(
        **base, status=DownloadStatus.DOWNLOADING,
        progress=ProgressInfo(status=DownloadStatus.DOWNLOADING, percent=99.4,
                              downloaded_bytes=994, total_bytes=1000,
                              total_is_estimate=True, speed=1.0, eta=1),
    ))
    row.update(TaskSnapshot(
        **base, status=DownloadStatus.COMPLETED,
        progress=ProgressInfo(status=DownloadStatus.COMPLETED, percent=100.0),
        file_path=str(DEST / "sample.mp4"),
    ))
    assert row.status_key == "completed"

    app.downloads._update_counts()
    assert "total" in app.downloads.counts_label.cget("text")

    # Clearing these hand-made rows (they aren't in the service) keeps the
    # page consistent with the (empty) service afterwards.
    removed = app.clear_finished_downloads()
    assert removed == 0
    assert not app.downloads.rows


phase("render every status", render_every_status)

# --------------------------------------------------------------------------- #
# Downloads: real queue + workers + history (fake engine, no network)
# --------------------------------------------------------------------------- #


def service_lifecycle() -> None:
    # 1. Success path.
    ok, task_id = app.download_service.submit(make_request("Happy path"))
    assert ok, task_id
    assert wait_for(lambda: status_of(task_id) is DownloadStatus.COMPLETED), \
        "success task did not complete"
    pump_events()  # let the row consume the final snapshot
    snap = app.download_service.get(task_id)
    assert snap and snap.file_path and Path(snap.file_path).is_file()
    assert str(app.downloads.rows[task_id].buttons["open_file"].cget("state")) == "normal"

    # 2. Duplicate URL while active is rejected.
    ok2, blocker_id = app.download_service.submit(make_request("Blocker task"))
    assert ok2, blocker_id
    assert wait_for(lambda: status_of(blocker_id) is DownloadStatus.DOWNLOADING), \
        "blocker never started"
    dup_ok, dup_msg = app.download_service.submit(make_request("Blocker task"))
    assert not dup_ok and "already" in dup_msg.lower(), (dup_ok, dup_msg)

    # 3. Cancelling a queued task is immediate (blocker occupies the slot).
    ok3, queued_id = app.download_service.submit(make_request("Waiting in line"))
    assert ok3, queued_id
    assert status_of(queued_id) is DownloadStatus.QUEUED
    cancelled, cancel_msg = app.download_service.cancel(queued_id)
    assert cancelled, cancel_msg
    assert status_of(queued_id) is DownloadStatus.CANCELLED
    assert app.download_service.get(queued_id).history_id is None  # never ran

    # 4. Cancelling a running task: honest async transition.
    accepted, _ = app.download_service.cancel(blocker_id)
    assert accepted
    assert wait_for(lambda: status_of(blocker_id) is DownloadStatus.CANCELLED), \
        "blocker did not cancel"

    # 5. Failure path.
    ok4, fail_id = app.download_service.submit(make_request("Doomed video"))
    assert ok4, fail_id
    assert wait_for(lambda: status_of(fail_id) is DownloadStatus.FAILED), \
        "failure task did not fail"
    assert "Simulated" in (app.download_service.get(fail_id).error or "")

    # 6. Retry re-queues the same task and runs again.
    retried, retry_msg = app.download_service.retry(fail_id)
    assert retried, retry_msg
    assert wait_for(lambda: status_of(fail_id) is DownloadStatus.FAILED), \
        "retry did not re-run"

    # 7. History rows: completed, cancelled(blocker), failed x2 (two runs).
    pump_events()
    statuses = sorted(r.status for r in app.history_repo.list())
    assert statuses == ["cancelled", "completed", "failed", "failed"], statuses

    # 8. Forget removes finished rows from the list, history is untouched.
    assert app.download_service.forget(task_id)
    app.downloads.remove_row(task_id)
    assert task_id not in app.downloads.rows
    assert app.history_count() == 4

    # 9. Clear every remaining finished item; page returns to its empty state.
    removed = app.clear_finished_downloads()
    assert removed == 3, removed  # blocker, fail (retried), ... + render leftovers
    assert not app.download_service.snapshots()
    assert not app.downloads.rows


phase("service lifecycle", service_lifecycle)

# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #


def history_flows() -> None:
    existing = DEST / "existing file.mp4"
    existing.write_bytes(b"\x00" * 16)
    extra = [
        ("Finished video", "https://example.com/watch?v=h1", "completed",
         str(existing), "1080p", "Merged → MP4"),
        ("Broken video", "https://example.com/watch?v=h2", "failed",
         str(DEST), "720p", "Source container"),
        ("Gone video", "https://example.com/watch?v=h3", "completed",
         str(DEST / "deleted.mp4"), "Best audio", "MP3"),
        ("Stopped early", "https://example.com/watch?v=h4", "interrupted",
         str(DEST), "1080p", "Merged → MP4"),
    ]
    baseline = app.history_count()
    for title, url, status, dest, quality, fmt in extra:
        record_id = app.history_repo.create(
            source_url=url, title=title, thumbnail_url=None, quality=quality,
            output_format=fmt, destination_path=dest, status=status,
        )
        if status == "failed":
            app.history_repo.update_status(
                record_id, "failed", error_message="Simulated error"
            )

    app.show("history")
    app.history_page.refresh()
    shown = len(app.history_page.list_frame.winfo_children())
    assert shown == baseline + 4, (shown, baseline)

    app.history_page.search_entry.insert(0, "Finished")
    app.history_page._on_search_key()
    assert len(app.history_page.list_frame.winfo_children()) == 1
    app.history_page.search_entry.delete(0, "end")
    app.history_page._on_search_key()

    app.history_page._on_status_change("Failed")
    failed_rows = len(app.history_page.list_frame.winfo_children())
    assert failed_rows == 3, failed_rows  # 2 lifecycle failures + 1 seeded
    app.history_page._on_status_change("All statuses")

    # Opening a missing file shows a warning instead of raising.
    missing = next(r for r in app.history_repo.list() if r.title == "Gone video")
    app.open_history_file(missing.id)

    deleted = next(r for r in app.history_repo.list() if r.title == "Finished video")
    app.delete_history_entry(deleted.id)
    assert app.history_repo.get(deleted.id) is None
    assert Path(existing).is_file(), "deleting history must never delete the file"

    app.history_page.refresh()


phase("history flows", history_flows)

# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def settings_flows() -> None:
    app.show("settings")
    app.settings_page.reload()
    app.settings_page._on_mode("Audio only")
    assert app.settings.default_mode == "audio"
    app.settings_page._on_quality("1080p (Full HD)")
    assert app.settings.preferred_quality == "1080p"
    app.settings_page._on_container("MKV")
    assert app.settings.video_container == "mkv"
    app.settings_page._on_concurrency("3")
    assert app.settings.max_concurrent == 3
    app.settings_page._on_appearance("Light")
    assert app.settings.appearance == "Light"
    app.toggle_appearance()  # Light -> Dark
    assert app.settings.appearance == "Dark"
    app.redetect_ffmpeg()
    app.settings_page._render_ffmpeg_status()
    app.settings_page.reload()
    # The page's own _reset_defaults opens a modal; exercise the controller
    # directly to stay non-interactive.
    app.reset_settings()
    app.settings_page.reload()
    assert app.settings.default_mode == "video"
    assert app.settings.max_concurrent == 1
    assert app.settings.video_container == "mp4"


phase("settings flows", settings_flows)

# --------------------------------------------------------------------------- #
# Dialogs + toasts + event pump
# --------------------------------------------------------------------------- #


def dialog_flows() -> None:
    from app.state import EVENT_METADATA, EVENT_TOAST
    from ui import dialogs

    message_box = dialogs._ModalDialog(app, "Title", "Message body",
                                       [("ok", "OK")], icon_name="info")
    message_box.update_idletasks()
    message_box.destroy()

    confirm_box = dialogs._ModalDialog(
        app, "Confirm", "Are you sure?", [("yes", "Yes"), ("no", "No")]
    )
    confirm_box.update_idletasks()
    confirm_box._choose("yes")
    assert confirm_box.result == "yes"

    app._toasts.show("Something happened", "success")
    app._toasts.show("Something failed", "error")
    app.update_idletasks()

    app.event_bus.post(EVENT_TOAST, ("info", "From the event bus"))

    # A stale metadata token must be ignored; the current token applies.
    app._fetching = True
    app.event_bus.post(EVENT_METADATA, (app._fetch_token - 1, None, "stale error"))
    for event in app.event_bus.drain(32):
        app._dispatch(event)
    assert app.home.url_error.cget("text") != "stale error"

    app.event_bus.post(EVENT_METADATA, (app._fetch_token, sample_info, None))
    for event in app.event_bus.drain(32):
        app._dispatch(event)
    assert not app._fetching
    assert app.home._info is not None


phase("dialog/toast/pump flows", dialog_flows)

# --------------------------------------------------------------------------- #
# Navigation across every page + a full render pass
# --------------------------------------------------------------------------- #


def navigation() -> None:
    for key in ("home", "downloads", "history", "settings", "home"):
        app.show(key)
    app.update()  # one bounded render pass (timers may fire once)
    geometry = app.container.winfo_geometry()
    assert geometry and "x" in geometry, geometry
    app.toggle_appearance()  # and back to dark for a second render pass
    app.update()


phase("navigation + render", navigation)

print()
if failures:
    print(f"{len(failures)} PHASE(S) FAILED")
    for failure in failures:
        print("-" * 70)
        print(failure)
    app.destroy()
    sys.exit(1)

print("UI EXERCISE OK")
app.destroy()
