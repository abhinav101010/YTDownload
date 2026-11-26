"""Downloads page: live view of queued/running/finished downloads."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import customtkinter as ctk

from downloader.progress import (
    DownloadStatus,
    TaskSnapshot,
    format_bytes,
    format_eta,
    format_speed,
)
from ui import icons, theme
from ui.components import Card, EmptyState, PageHeader, StatusBadge, small_button

if TYPE_CHECKING:  # pragma: no cover
    from app.application import YTDownloaderApp

logger = logging.getLogger(__name__)

_THUMB_W, _THUMB_H = 108, 61


class DownloadRow(Card):
    """One download: thumbnail, title, live progress and state actions."""

    def __init__(
        self,
        master,
        snapshot: TaskSnapshot,
        *,
        on_cancel: Callable[[str], None],
        on_retry: Callable[[str], None],
        on_open_file: Callable[[str], None],
        on_open_folder: Callable[[str], None],
        on_remove: Callable[[str], None],
    ) -> None:
        super().__init__(master)
        self.task_id = snapshot.id
        self._on_cancel = on_cancel
        self._on_retry = on_retry
        self._on_open_file = on_open_file
        self._on_open_folder = on_open_folder
        self._on_remove = on_remove
        self._progress_mode = "determinate"

        self.grid_columnconfigure(1, weight=1)

        self.thumb = ctk.CTkLabel(
            self, text="", width=_THUMB_W, height=_THUMB_H, corner_radius=8,
            fg_color=theme.INPUT_BG, image=icons.placeholder(30), compound="center",
        )
        self.thumb.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(14, 12), pady=14)

        title_row = ctk.CTkFrame(self, fg_color="transparent")
        title_row.grid(row=0, column=1, sticky="ew", pady=(12, 0))
        title_row.grid_columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(
            title_row, text=snapshot.title, font=theme.font(13, "bold"),
            text_color=theme.TEXT, fg_color="transparent", anchor="w", justify="left",
        )
        self.title_label.grid(row=0, column=0, sticky="w")
        self.badge = StatusBadge(title_row, snapshot.status.value)
        self.badge.grid(row=0, column=1, sticky="e", padx=(8, 0))

        self.meta_label = ctk.CTkLabel(
            self, text="", font=theme.font(11), text_color=theme.TEXT_DIM,
            fg_color="transparent", anchor="w", justify="left",
        )
        self.meta_label.grid(row=1, column=1, sticky="ew", pady=(2, 0))

        progress_row = ctk.CTkFrame(self, fg_color="transparent")
        progress_row.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        progress_row.grid_columnconfigure(0, weight=1)
        self.progress_bar = ctk.CTkProgressBar(progress_row, height=10, corner_radius=5)
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.percent_label = ctk.CTkLabel(
            progress_row, text="", width=52, font=theme.font(11, "bold"),
            text_color=theme.TEXT_DIM, fg_color="transparent",
        )
        self.percent_label.grid(row=0, column=1)

        self.error_label = ctk.CTkLabel(
            self, text="", font=theme.font(11), text_color=theme.DANGER,
            fg_color="transparent", anchor="w", justify="left",
        )

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=3, column=1, sticky="ew", pady=(8, 10))
        self.buttons: dict[str, ctk.CTkButton] = {}
        self._handlers: dict[str, Callable[[str], None]] = {
            "cancel": on_cancel,
            "retry": on_retry,
            "open_file": on_open_file,
            "open_folder": on_open_folder,
            "remove": on_remove,
        }
        for key, label, icon_name, variant in (
            ("cancel", "Cancel", "cancel", "default"),
            ("retry", "Retry", "retry", "default"),
            ("open_file", "Open", "open", "default"),
            ("open_folder", "Folder", "folder", "default"),
            ("remove", "Remove", "trash", "danger"),
        ):
            button = small_button(
                actions, label, lambda k=key: self._fire(k),
                icon_name=icon_name, variant=variant,
            )
            button.pack(side="left", padx=(0, 6))
            self.buttons[key] = button

        self.status_key = snapshot.status.value
        self.update(snapshot)

    def _fire(self, key: str) -> None:
        handler = self._handlers.get(key)
        if handler:
            handler(self.task_id)

    # ------------------------------------------------------------------ #

    def update(self, snapshot: TaskSnapshot) -> None:
        status = snapshot.status
        status_key = status.value
        self.status_key = status_key

        self.title_label.configure(text=snapshot.title or "Untitled")
        self.badge.set_status(status_key)

        # Meta line: quality · format · size/speed/eta.
        meta = [snapshot.quality_label, snapshot.format_label]
        progress = snapshot.progress
        if status in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING):
            if progress.downloaded_bytes is not None:
                total = (
                    f" of {format_bytes(progress.total_bytes)}"
                    + (" (est.)" if progress.total_is_estimate and progress.total_bytes else "")
                    if progress.total_bytes
                    else ""
                )
                meta.append(f"{format_bytes(progress.downloaded_bytes)}{total}")
            if status is DownloadStatus.DOWNLOADING:
                meta.append(f"{format_speed(progress.speed)} · ETA {format_eta(progress.eta)}")
            elif progress.stage:
                meta.append(progress.stage)
        elif status is DownloadStatus.COMPLETED and snapshot.file_path:
            meta.append(Path(snapshot.file_path).name)
        elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED) and snapshot.error:
            meta.append(snapshot.error)
        self.meta_label.configure(text="  ·  ".join(part for part in meta if part))

        # Progress bar + percentage (never 100% before processing finishes).
        if status is DownloadStatus.COMPLETED:
            self._set_bar(1.0, "100%")
        elif status is DownloadStatus.DOWNLOADING and progress.percent is not None:
            self._set_bar(progress.percent / 100.0, f"{progress.percent:.1f}%")
        elif status in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING):
            self._set_indeterminate(progress.stage or "Working…")
        elif status is DownloadStatus.QUEUED:
            self._set_indeterminate("Waiting for an open slot…")
        else:  # failed / cancelled — keep the last real percentage if any
            if progress.percent is not None:
                self._set_bar(progress.percent / 100.0, f"{progress.percent:.0f}%")
            else:
                self._set_bar(0.0, "—")

        # Error line.
        if status is DownloadStatus.FAILED and snapshot.error:
            self.error_label.configure(text=f"⚠ {snapshot.error}")
            self.error_label.grid(row=4, column=1, sticky="ew", pady=(4, 0))
        elif status is DownloadStatus.CANCELLED:
            self.error_label.configure(text="Cancelled — you can retry or remove it.")
            self.error_label.grid(row=4, column=1, sticky="ew", pady=(4, 0))
        else:
            self.error_label.grid_forget()

        # Availability of actions.
        active = status in (
            DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING
        )
        terminal_failed = status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED)
        self.buttons["cancel"].configure(state="normal" if active else "disabled")
        self.buttons["retry"].configure(state="normal" if terminal_failed else "disabled")
        file_ready = status is DownloadStatus.COMPLETED and bool(snapshot.file_path)
        self.buttons["open_file"].configure(state="normal" if file_ready else "disabled")
        self.buttons["open_folder"].configure(
            state="normal" if (file_ready or status is DownloadStatus.FAILED) else "disabled"
        )
        self.buttons["remove"].configure(state="disabled" if active else "normal")

    def _set_bar(self, value: float, text: str) -> None:
        if self._progress_mode != "determinate":
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self._progress_mode = "determinate"
        self.progress_bar.set(max(0.0, min(1.0, value)))
        self.percent_label.configure(text=text)

    def _set_indeterminate(self, text: str) -> None:
        if self._progress_mode != "indeterminate":
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
            self._progress_mode = "indeterminate"
        self.percent_label.configure(text="…")

    def apply_thumbnail(self, image) -> None:
        try:
            from PIL import Image

            ratio = min(_THUMB_W / image.width, _THUMB_H / image.height)
            size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
            resized = image.resize(size, Image.LANCZOS)
            ctk_image = ctk.CTkImage(dark_image=resized, light_image=resized, size=size)
            self.thumb.configure(image=ctk_image, text="")
        except Exception:
            logger.debug("Could not render row thumbnail", exc_info=True)


class DownloadsPage(ctk.CTkFrame):
    """List of download rows fed by EVENT_DOWNLOAD snapshots."""

    def __init__(self, master, controller: "YTDownloaderApp") -> None:
        super().__init__(master, fg_color="transparent")
        self.controller = controller
        self.rows: dict[str, DownloadRow] = {}

        header = PageHeader(self, "Downloads", "Queue, progress and results.")
        header.pack(fill="x", padx=theme.PAGE_PAD, pady=(theme.PAGE_PAD, 14))
        self.counts_label = ctk.CTkLabel(
            header.actions, text="", font=theme.font(12), text_color=theme.TEXT_DIM,
            fg_color="transparent",
        )
        self.counts_label.pack(side="left", padx=(0, 10))
        small_button(
            header.actions, "Clear finished", self._clear_finished,
            icon_name="clear", variant="solid",
        ).pack(side="left")

        self.empty_state = EmptyState(
            self,
            "download",
            "Nothing here yet",
            "Start a download from the Home page — it will appear here with live "
            "progress, and you can cancel or retry it.",
        )
        self.empty_state.pack(fill="both", expand=True)

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def apply_snapshot(self, snapshot: TaskSnapshot) -> None:
        row = self.rows.get(snapshot.id)
        if row is None:
            self.empty_state.pack_forget()
            self.list_frame.pack(fill="both", expand=True)
            row = DownloadRow(
                self.list_frame,
                snapshot,
                on_cancel=self.controller.cancel_download,
                on_retry=self.controller.retry_download,
                on_open_file=self.controller.open_download_file,
                on_open_folder=self.controller.open_download_folder,
                on_remove=self.controller.remove_download,
            )
            self.rows[snapshot.id] = row
            row.pack(fill="x", pady=(0, 10))
        else:
            row.update(snapshot)
        self._update_counts()

    def remove_row(self, task_id: str) -> None:
        row = self.rows.pop(task_id, None)
        if row is not None:
            row.destroy()
        if not self.rows:
            self.list_frame.pack_forget()
            self.empty_state.pack(fill="both", expand=True)
        self._update_counts()

    def clear_rows(self) -> None:
        for row in self.rows.values():
            row.destroy()
        self.rows.clear()
        self.list_frame.pack_forget()
        self.empty_state.pack(fill="both", expand=True)
        self._update_counts()

    def apply_thumbnail(self, task_id: str, image) -> None:
        row = self.rows.get(task_id)
        if row is not None and image is not None:
            row.apply_thumbnail(image)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _clear_finished(self) -> None:
        removed = self.controller.clear_finished_downloads()
        if removed:
            self.controller.toast(f"Removed {removed} finished item(s) from the list.", "info")
        else:
            self.controller.toast("There are no finished downloads to clear.", "info")

    def _update_counts(self) -> None:
        active = sum(
            1 for row in self.rows.values()
            if row.status_key in ("queued", "downloading", "processing")
        )
        total = len(self.rows)
        if total:
            text = f"{active} active · {total} total" if active else f"{total} total"
        else:
            text = ""
        self.counts_label.configure(text=text)
