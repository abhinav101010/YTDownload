"""History page: persistent download log backed by SQLite."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from database.models import HistoryRecord
from ui import dialogs, icons, theme
from ui.components import Card, EmptyState, PageHeader, StatusBadge, small_button

if TYPE_CHECKING:  # pragma: no cover
    from app.application import YTDownloaderApp

logger = logging.getLogger(__name__)

_FILTER_LABELS = {
    "all": "All statuses",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "interrupted": "Interrupted",
}


class HistoryRow(Card):
    def __init__(
        self,
        master,
        record: HistoryRecord,
        *,
        on_open_file,
        on_open_folder,
        on_delete,
    ) -> None:
        super().__init__(master)
        self.record_id = record.id
        self._handlers = {
            "open_file": on_open_file,
            "open_folder": on_open_folder,
            "delete": on_delete,
        }

        self.grid_columnconfigure(1, weight=1)

        self.status_icon = ctk.CTkLabel(
            self, text="", width=42, height=42, corner_radius=8,
            fg_color=theme.INPUT_BG, image=icons.icon("film", 20), compound="center",
        )
        self.status_icon.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(14, 12), pady=13)

        title_row = ctk.CTkFrame(self, fg_color="transparent")
        title_row.grid(row=0, column=1, sticky="ew", pady=(12, 0))
        title_row.grid_columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(
            title_row, text=record.title, font=theme.font(13, "bold"),
            text_color=theme.TEXT, fg_color="transparent", anchor="w", justify="left",
            wraplength=520,
        )
        self.title_label.grid(row=0, column=0, sticky="w")
        self.badge = StatusBadge(title_row, record.status)
        self.badge.grid(row=0, column=1, sticky="e", padx=(8, 0))

        meta = [record.created_at_display]
        if record.quality:
            meta.append(record.quality)
        if record.output_format:
            meta.append(record.output_format)
        meta.append(record.source_url)
        self.meta_label = ctk.CTkLabel(
            self, text="  ·  ".join(meta), font=theme.font(11),
            text_color=theme.TEXT_DIM, fg_color="transparent", anchor="w",
            justify="left", wraplength=560,
        )
        self.meta_label.grid(row=1, column=1, sticky="ew", pady=(2, 0))

        path_text = record.destination_path or "Location unknown"
        if record.file_state() == "available":
            path_text = str(record.destination_path)
        self.path_label = ctk.CTkLabel(
            self, text=path_text, font=theme.font(11), text_color=theme.TEXT_FAINT,
            fg_color="transparent", anchor="w", justify="left", wraplength=560,
        )
        self.path_label.grid(row=2, column=1, sticky="ew", pady=(2, 0))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=1, sticky="ew", pady=(8, 11))

        if record.status == "completed" and not record.file_available:
            ctk.CTkLabel(
                bottom, text="⚠ File no longer exists at that location",
                font=theme.font(11, "bold"), text_color=theme.WARNING,
                fg_color="transparent",
            ).pack(side="left", padx=(0, 10))
        if record.status == "failed" and record.error_message:
            ctk.CTkLabel(
                bottom, text=f"⚠ {record.error_message}",
                font=theme.font(11), text_color=theme.DANGER, fg_color="transparent",
                wraplength=420, justify="left",
            ).pack(side="left", padx=(0, 10))

        self.buttons: dict[str, ctk.CTkButton] = {}
        for key, label, icon_name, variant in (
            ("open_file", "Open file", "open", "default"),
            ("open_folder", "Show in folder", "folder", "default"),
            ("delete", "Delete entry", "trash", "danger"),
        ):
            button = small_button(
                bottom, label, lambda k=key: self._fire(k), icon_name=icon_name, variant=variant
            )
            button.pack(side="right", padx=(6, 0))
            self.buttons[key] = button

        self.buttons["open_file"].configure(
            state="normal" if record.file_available else "disabled"
        )
        self.buttons["open_folder"].configure(
            state="normal" if record.file_state() != "unknown" else "disabled"
        )

    def _fire(self, key: str) -> None:
        handler = self._handlers.get(key)
        if handler:
            handler(self.record_id)


class HistoryPage(ctk.CTkFrame):
    """Searchable, filterable download history."""

    def __init__(self, master, controller: "YTDownloaderApp") -> None:
        super().__init__(master, fg_color="transparent")
        self.controller = controller
        self._search = ""
        self._status_filter = "all"

        header = PageHeader(self, "History", "Everything you've downloaded, kept on this Mac.")
        header.pack(fill="x", padx=theme.PAGE_PAD, pady=(theme.PAGE_PAD, 12))
        small_button(
            header.actions, "Clear history", self._clear_history,
            icon_name="trash", variant="danger",
        ).pack(side="right")

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=theme.PAGE_PAD, pady=(0, 12))

        search_box = ctk.CTkFrame(
            toolbar, fg_color=theme.INPUT_BG, border_width=1, border_color=theme.BORDER,
            corner_radius=theme.RADIUS_BUTTON, height=36,
        )
        search_box.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ctk.CTkLabel(
            search_box, text="", image=icons.icon("search", 15), fg_color="transparent",
        ).pack(side="left", padx=(10, 6))
        self.search_entry = ctk.CTkEntry(
            search_box, placeholder_text="Search by title or URL…", border_width=0,
            fg_color="transparent", text_color=theme.TEXT, font=theme.font(12), height=30,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=3)
        self.search_entry.bind("<KeyRelease>", self._on_search_key)

        self.status_menu = ctk.CTkOptionMenu(
            toolbar,
            values=list(_FILTER_LABELS.values()),
            width=150,
            height=36,
            corner_radius=theme.RADIUS_BUTTON,
            fg_color=theme.INPUT_BG,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT,
            dropdown_fg_color=theme.CARD,
            text_color=theme.TEXT,
            font=theme.font(12),
            command=self._on_status_change,
        )
        self.status_menu.set(_FILTER_LABELS["all"])
        self.status_menu.pack(side="left")

        self.empty_state = EmptyState(
            self, "history", "No history yet",
            "Completed, failed and cancelled downloads are recorded here — "
            "searchable and persistent across restarts.",
        )
        self.empty_state.pack(fill="both", expand=True)

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.refresh()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        """Reload rows from the database (respects search + filter)."""
        for child in self.list_frame.winfo_children():
            child.destroy()

        try:
            records = self.controller.history_records(
                search=self._search, status=self._status_filter
            )
        except Exception:
            logger.exception("Failed to load history")
            records = []

        if not records:
            self.list_frame.pack_forget()
            self.empty_state.pack(fill="both", expand=True)
            if self._search or self._status_filter != "all":
                self.empty_state.set_text(
                    "No matches",
                    "Nothing matches the current search/filter. Try clearing them.",
                )
            else:
                self.empty_state.set_text(
                    "No history yet",
                    "Completed, failed and cancelled downloads are recorded here — "
                    "searchable and persistent across restarts.",
                )
            return

        self.empty_state.pack_forget()
        self.list_frame.pack(fill="both", expand=True)
        for record in records:
            row = HistoryRow(
                self.list_frame,
                record,
                on_open_file=self._open_file,
                on_open_folder=self._open_folder,
                on_delete=self._delete_entry,
            )
            row.pack(fill="x", pady=(0, 10))

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    def _on_search_key(self, _event=None) -> None:
        self._search = self.search_entry.get().strip()
        self.refresh()

    def _on_status_change(self, label: str) -> None:
        for key, text in _FILTER_LABELS.items():
            if text == label:
                self._status_filter = key
                break
        self.refresh()

    def _open_file(self, record_id: int) -> None:
        self.controller.open_history_file(record_id)

    def _open_folder(self, record_id: int) -> None:
        self.controller.open_history_folder(record_id)

    def _delete_entry(self, record_id: int) -> None:
        self.controller.delete_history_entry(record_id)
        self.refresh()

    def _clear_history(self) -> None:
        count = self.controller.history_count()
        if not count:
            dialogs.show_message(self, "Nothing to clear", "The history is already empty.")
            return
        if dialogs.confirm(
            self,
            "Clear all history?",
            f"This removes all {count} history entries from the app. "
            "Downloaded files on disk are NOT deleted.",
            confirm_label="Clear history",
            destructive=True,
        ):
            self.controller.clear_all_history()
            self.refresh()
