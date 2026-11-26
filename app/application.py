"""Application bootstrap: main window, navigation, wiring and shutdown.

Threading model:

* All network/IO (metadata, thumbnails, downloads) runs in worker threads.
* Workers publish :class:`~app.state.Event` objects on the
  :class:`~app.state.EventBus`.
* The Tk main loop drains the bus with ``root.after(...)`` and is the only
  place where widgets are touched.
"""

from __future__ import annotations

import logging
import threading
from functools import partial
from typing import Callable, Optional

import customtkinter as ctk

from app import config
from app.state import (
    AppSettings,
    EVENT_DOWNLOAD,
    EVENT_METADATA,
    EVENT_SYSTEM,
    EVENT_THUMBNAIL,
    EVENT_TOAST,
    EventBus,
)
from database.database import Database
from database.models import HistoryRecord
from database.repositories import HistoryRepository, SettingsRepository
from downloader.engine import DownloadRequest
from downloader.metadata import (
    MediaInfo,
    MetadataError,
    fetch_media_info,
    load_thumbnail,
)
from downloader.progress import TaskSnapshot
from downloader.validators import (
    InvalidURLError,
    is_whole_channel_or_playlist,
    normalize_url,
)
from database.database import DatabaseError
from services import file_service, media_service
from services.download_service import DownloadService
from services.file_service import FileActionError
from services.media_service import FFmpegInfo, detect_ffmpeg
from ui.components import Sidebar, ToastManager
from ui.downloads_page import DownloadsPage
from ui.history_page import HistoryPage
from ui.home_page import HomePage
from ui.settings_page import SettingsPage

logger = logging.getLogger(__name__)

PAGE_HOME = "home"
PAGE_DOWNLOADS = "downloads"
PAGE_HISTORY = "history"
PAGE_SETTINGS = "settings"


class YTDownloaderApp(ctk.CTk):
    """Main window and controller for all pages/services."""

    def __init__(self, *, smoke_test: bool = False) -> None:
        super().__init__()
        self._smoke_test = smoke_test

        # ------------------------------------------------------------------ #
        # Persistence + services (before any widget construction)
        # ------------------------------------------------------------------ #
        self.db = Database().open()
        self.settings_repo = SettingsRepository(self.db)
        self.history_repo = HistoryRepository(self.db)
        self.settings = AppSettings.from_dict(self.settings_repo.load())
        self.event_bus = EventBus()
        self.ffmpeg_info: FFmpegInfo = detect_ffmpeg(self.settings.ffmpeg_path)

        try:
            recovered = self.history_repo.recover_stale()
            if recovered:
                logger.info("Recovered %d interrupted download(s) from a previous run", recovered)
        except DatabaseError:
            logger.warning("Could not recover stale history rows", exc_info=True)

        self.download_service = DownloadService(
            settings_provider=lambda: self.settings,
            event_bus=self.event_bus,
            ffmpeg_provider=lambda: self.ffmpeg_info,
            history=self.history_repo,
        )

        # ------------------------------------------------------------------ #
        # Window + theme
        # ------------------------------------------------------------------ #
        ctk.set_appearance_mode(self.settings.appearance)
        ctk.set_default_color_theme(self.settings.accent_theme)

        self.title(f"{config.APP_NAME}")
        self.geometry("1180x770")
        self.minsize(1000, 660)
        self.configure(fg_color=("#eef1f6", "#0e1116"))

        # ------------------------------------------------------------------ #
        # Layout: sidebar + page container
        # ------------------------------------------------------------------ #
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(
            self, on_navigate=self.show, on_toggle_appearance=self.toggle_appearance
        )
        self.sidebar.grid(row=0, column=0, sticky="nsw")

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew")
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_rowconfigure(0, weight=1)

        self.home = HomePage(self.container, self)
        self.downloads = DownloadsPage(self.container, self)
        self.history_page = HistoryPage(self.container, self)
        self.settings_page = SettingsPage(self.container, self)
        self._pages = {
            PAGE_HOME: self.home,
            PAGE_DOWNLOADS: self.downloads,
            PAGE_HISTORY: self.history_page,
            PAGE_SETTINGS: self.settings_page,
        }

        self._toasts = ToastManager(self)
        self._fetching = False
        self._fetch_token = 0
        self._thumb_jobs: set[tuple[str, str]] = set()
        self._row_thumb_tasks: set[str] = set()

        # ------------------------------------------------------------------ #
        # Startup behaviour
        # ------------------------------------------------------------------ #
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.show(PAGE_HOME)
        self.sidebar.set_appearance_label(self._appearance_is_dark())

        self.after(90, self._poll_events)
        if self.settings.check_updates_on_start:
            self.after(1200, self._startup_update_check)
        if smoke_test:
            self.after(1500, self._smoke_shutdown)

    # ================================================================== #
    # Navigation
    # ================================================================== #

    def show(self, page_key: str) -> None:
        page = self._pages.get(page_key)
        if page is None:
            return
        for name, other in self._pages.items():
            if name == page_key:
                other.pack(fill="both", expand=True)
            else:
                other.pack_forget()
        self.sidebar.set_selected(page_key)

        if page_key == PAGE_DOWNLOADS:
            self._refresh_downloads_page()
        elif page_key == PAGE_HISTORY:
            self.history_page.refresh()
        elif page_key == PAGE_SETTINGS:
            self.settings_page.reload()

    def _refresh_downloads_page(self) -> None:
        """Rebuild rows from the service so the page always matches reality."""
        self.downloads.clear_rows()
        for snapshot in self.download_service.snapshots():
            self.downloads.apply_snapshot(snapshot)
            if snapshot.thumbnail_url:
                self._request_task_thumbnail(snapshot.id, snapshot.thumbnail_url)

    # ================================================================== #
    # Event pump (worker threads -> Tk main loop)
    # ================================================================== #

    def _poll_events(self) -> None:
        for event in self.event_bus.drain(64):
            try:
                self._dispatch(event)
            except Exception:
                logger.exception("Error while handling %s event", event.kind)
        if self.winfo_exists():
            self.after(90, self._poll_events)

    def _dispatch(self, event) -> None:
        if event.kind == EVENT_DOWNLOAD:
            snapshot: TaskSnapshot = event.payload
            self.downloads.apply_snapshot(snapshot)
            if snapshot.thumbnail_url and snapshot.id not in self._row_thumb_tasks:
                self._request_task_thumbnail(snapshot.id, snapshot.thumbnail_url)
        elif event.kind == EVENT_METADATA:
            token, info, error = event.payload
            self._on_metadata_result(token, info, error)
        elif event.kind == EVENT_THUMBNAIL:
            _key, image, consumer = event.payload
            if image is not None:
                consumer(image)
        elif event.kind == EVENT_TOAST:
            level, message = event.payload or ("info", "")
            self._toasts.show(message, level)
        elif event.kind == EVENT_SYSTEM:
            self._on_system_event(event.payload)

    def _on_metadata_result(self, token: int, info: Optional[MediaInfo], error: Optional[str]) -> None:
        self._fetching = False
        if token != self._fetch_token:
            return  # stale result from a cancelled/superseded request
        self.home.set_loading(False)
        if error:
            self.home.show_url_error(error)
            return
        if info is None:
            self.home.show_url_error("No information was returned for that URL.")
            return
        self.home.show_metadata(info)

    def _on_system_event(self, payload) -> None:
        if not isinstance(payload, tuple) or not payload or payload[0] != "update_check":
            return
        data = payload[1]
        # "check_yt_dlp_update_now" posts (callback, result); startup posts
        # the bare result.
        if isinstance(data, tuple) and len(data) == 2 and callable(data[0]):
            callback, result = data
            callback(result)
            return
        result = data
        if result is not None and not isinstance(result, str):
            installed, latest = result
            self.toast(
                f"yt-dlp update available: {installed} → {latest} "
                "(see Settings for details)",
                "warning",
            )

    # ================================================================== #
    # Home page actions
    # ================================================================== #

    def fetch_metadata(self, raw_url: str) -> None:
        if self._fetching:
            return
        try:
            url = normalize_url(raw_url)
        except InvalidURLError as exc:
            self.home.show_url_error(str(exc))
            return
        if is_whole_channel_or_playlist(url):
            self.home.show_url_error(
                "That looks like a channel or playlist. Paste a link to a single video."
            )
            return

        self._fetching = True
        self._fetch_token += 1
        token = self._fetch_token
        self.home.set_loading(True)

        def worker() -> None:
            try:
                info = fetch_media_info(url)
            except MetadataError as exc:
                self.event_bus.post(EVENT_METADATA, (token, None, str(exc)))
            except Exception:
                logger.exception("Unexpected metadata failure")
                self.event_bus.post(
                    EVENT_METADATA,
                    (token, None, "An unexpected error occurred while reading that URL."),
                )
            else:
                self.event_bus.post(EVENT_METADATA, (token, info, None))

        threading.Thread(target=worker, name="ytdl-metadata", daemon=True).start()

    def request_thumbnail(self, url: str) -> None:
        self._thumbnail_job("home", url, partial(self.home.show_thumbnail, url))

    def _request_task_thumbnail(self, task_id: str, url: str) -> None:
        self._row_thumb_tasks.add(task_id)
        self._thumbnail_job(
            f"task:{task_id}", url, partial(self.downloads.apply_thumbnail, task_id)
        )

    def _thumbnail_job(self, tag: str, url: str, deliver) -> None:
        key = (tag, url)
        if key in self._thumb_jobs:
            return
        self._thumb_jobs.add(key)

        def worker() -> None:
            try:
                image = load_thumbnail(url)
            except Exception:
                logger.debug("Thumbnail load failed", exc_info=True)
                image = None
            self.event_bus.post(EVENT_THUMBNAIL, (key, image, deliver))

        threading.Thread(target=worker, name="ytdl-thumbnail", daemon=True).start()

    def queue_download(self, request: DownloadRequest) -> tuple[bool, str]:
        return self.download_service.submit(request)

    # ================================================================== #
    # Downloads page actions
    # ================================================================== #

    def cancel_download(self, task_id: str) -> None:
        accepted, message = self.download_service.cancel(task_id)
        self.toast(message, "info" if accepted else "warning")

    def retry_download(self, task_id: str) -> None:
        accepted, message = self.download_service.retry(task_id)
        self.toast(message, "info" if accepted else "warning")

    def remove_download(self, task_id: str) -> None:
        if self.download_service.forget(task_id):
            self.downloads.remove_row(task_id)
            self._row_thumb_tasks.discard(task_id)
        else:
            self.toast("Cancel the download before removing it.", "warning")

    def clear_finished_downloads(self) -> int:
        removed = self.download_service.clear_finished()
        self._refresh_downloads_page()
        return removed

    def open_download_file(self, task_id: str) -> None:
        snapshot = self.download_service.get(task_id)
        try:
            file_service.open_file(snapshot.file_path if snapshot else None)
        except FileActionError as exc:
            self.toast(str(exc), "error")

    def open_download_folder(self, task_id: str) -> None:
        snapshot = self.download_service.get(task_id)
        if snapshot is None:
            self.toast("That download no longer exists.", "warning")
            return
        target = snapshot.file_path or snapshot.destination
        try:
            file_service.open_containing_folder(target)
        except FileActionError as exc:
            self.toast(str(exc), "error")

    # ================================================================== #
    # History actions
    # ================================================================== #

    def history_records(self, *, search: str = "", status: str = "all") -> list[HistoryRecord]:
        return self.history_repo.list(search=search, status=status)

    def history_count(self) -> int:
        return self.history_repo.count()

    def open_history_file(self, record_id: int) -> None:
        record = self.history_repo.get(record_id)
        if record is None:
            self.toast("That entry no longer exists.", "warning")
            return
        if not record.file_available:
            self.toast("That file no longer exists on disk.", "warning")
            return
        try:
            file_service.open_file(record.destination_path)
        except FileActionError as exc:
            self.toast(str(exc), "error")

    def open_history_folder(self, record_id: int) -> None:
        record = self.history_repo.get(record_id)
        if record is None:
            self.toast("That entry no longer exists.", "warning")
            return
        try:
            file_service.open_containing_folder(record.destination_path)
        except FileActionError as exc:
            self.toast(str(exc), "error")

    def delete_history_entry(self, record_id: int) -> None:
        if self.history_repo.delete(record_id):
            self.toast("History entry removed (the file on disk was kept).", "info")

    def clear_all_history(self) -> None:
        removed = self.history_repo.clear()
        self.toast(f"Removed {removed} history entries (files on disk were kept).", "info")

    # ================================================================== #
    # Settings actions
    # ================================================================== #

    @property
    def yt_dlp_version(self) -> str:
        return media_service.yt_dlp_version()

    def save_settings(self, *, announce: bool = False, **changes: object) -> None:
        data = self.settings.to_dict()
        data.update({key: str(value) for key, value in changes.items()})
        self.settings_repo.save(data)
        self.settings = AppSettings.from_dict(self.settings_repo.load())
        self._apply_settings(set(changes))
        if announce:
            self.toast("Settings saved", "success")

    def reset_settings(self) -> None:
        self.settings_repo.reset()
        self.settings = AppSettings.from_dict(self.settings_repo.load())
        self._apply_settings(set(config.DEFAULT_SETTINGS))
        self.toast("Settings reset to defaults.", "info")

    def _apply_settings(self, changed_keys: set[str]) -> None:
        ctk.set_appearance_mode(self.settings.appearance)
        self.sidebar.set_appearance_label(self._appearance_is_dark())
        if "accent_theme" in changed_keys:
            ctk.set_default_color_theme(self.settings.accent_theme)
            self.toast("Accent colour saved — restart the app to see it.", "info")
        if "max_concurrent" in changed_keys:
            self.download_service.on_settings_changed()
        if "ffmpeg_path" in changed_keys:
            self.redetect_ffmpeg()
            self.home.refresh_availability()

    def toggle_appearance(self) -> None:
        new_mode = "Light" if self._appearance_is_dark() else "Dark"
        self.save_settings(appearance=new_mode, announce=False)
        self.toast(f"Switched to {new_mode.lower()} mode.", "info")

    def _appearance_is_dark(self) -> bool:
        return ctk.get_appearance_mode().lower() != "light"

    def redetect_ffmpeg(self) -> FFmpegInfo:
        self.ffmpeg_info = detect_ffmpeg(self.settings.ffmpeg_path)
        return self.ffmpeg_info

    def check_yt_dlp_update_now(self, callback: Callable[[object], None]) -> None:
        def worker() -> None:
            try:
                result: object = media_service.check_for_ytdlp_update()
            except Exception as exc:
                logger.info("Update check failed: %s", exc)
                result = "Could not reach the update server — check your connection."
            self.event_bus.post(EVENT_SYSTEM, ("update_check", (callback, result)))

        threading.Thread(target=worker, name="ytdl-update-check", daemon=True).start()

    def _startup_update_check(self) -> None:
        def worker() -> None:
            try:
                result = media_service.check_for_ytdlp_update()
            except Exception as exc:
                logger.debug("Startup update check failed: %s", exc)
                return
            if result is not None:
                self.event_bus.post(EVENT_SYSTEM, ("update_check", result))

        threading.Thread(target=worker, name="ytdl-update-start", daemon=True).start()

    # ================================================================== #
    # Toasts
    # ================================================================== #

    def toast(self, message: str, level: str = "info") -> None:
        self.event_bus.post(EVENT_TOAST, (level, message))

    # ================================================================== #
    # Shutdown
    # ================================================================== #

    def on_close(self) -> None:
        pending = self.download_service.pending_count
        if pending:
            from ui import dialogs

            quit_anyway = dialogs.confirm(
                self,
                "Downloads in progress",
                f"{pending} download(s) are still queued or running. "
                "Quit anyway? Active downloads will be cancelled.",
                confirm_label="Quit",
                destructive=True,
            )
            if not quit_anyway:
                return
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.after(20, self._shutdown_and_exit)

    def _shutdown_and_exit(self) -> None:
        try:
            self.download_service.shutdown(timeout=3.0)
        except Exception:
            logger.exception("Error while stopping downloads")
        try:
            self.settings_repo.save(self.settings.to_dict())
        except Exception:
            logger.exception("Could not save settings on exit")
        try:
            self.db.close()
        except Exception:
            logger.exception("Could not close the database")
        self.destroy()

    def _smoke_shutdown(self) -> None:
        """Automated launch check: start, render, exit (prints SMOKE OK)."""
        try:
            self.download_service.shutdown(timeout=1.0)
            self.settings_repo.save(self.settings.to_dict())
            self.db.close()
        except Exception:
            logger.exception("Smoke test shutdown encountered an error")
        print("SMOKE OK")
        self.destroy()
