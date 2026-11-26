"""Settings page: persisted preferences, FFmpeg status and update checks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import customtkinter as ctk

from app import config
from ui import dialogs, theme
from ui.components import Card, PageHeader, small_button

if TYPE_CHECKING:  # pragma: no cover
    from app.application import YTDownloaderApp

logger = logging.getLogger(__name__)

_QUALITY_CHOICES = {
    "Best available": "best",
    "2160p (4K)": "2160p",
    "1440p (QHD)": "1440p",
    "1080p (Full HD)": "1080p",
    "720p (HD)": "720p",
    "480p": "480p",
    "360p": "360p",
    "MP3 (audio)": "audio:mp3",
    "Best audio": "audio:best",
}
_MODE_CHOICES = {"Video (with audio)": "video", "Audio only": "audio"}
_ACCENT_CHOICES = {"Blue": "blue", "Green": "green", "Dark blue": "dark-blue"}
_APPEARANCE_CHOICES = {"Dark": "Dark", "Light": "Light", "System": "System"}


def _invert(mapping: dict[str, str]) -> dict[str, str]:
    return {value: label for label, value in mapping.items()}


class SettingsPage(ctk.CTkFrame):
    """All preferences save immediately; destructive actions confirm first."""

    def __init__(self, master, controller: "YTDownloaderApp") -> None:
        super().__init__(master, fg_color="transparent")
        self.controller = controller

        PageHeader(
            self, "Settings", "Preferences are saved instantly on this Mac."
        ).pack(fill="x", padx=theme.PAGE_PAD, pady=(theme.PAGE_PAD, 14))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True, padx=(theme.PAGE_PAD - 6, theme.PAGE_PAD - 6))

        self._build_downloads(body)
        self._build_appearance(body)
        self._build_application(body)
        self._build_danger(body)

    # ------------------------------------------------------------------ #
    # Sections
    # ------------------------------------------------------------------ #

    def _section(self, master, title: str) -> tuple[Card, ctk.CTkFrame]:
        card = Card(master)
        card.pack(fill="x", padx=6, pady=(0, 14))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        ctk.CTkLabel(
            inner, text=title.upper(), font=theme.font(11, "bold"),
            text_color=theme.TEXT_FAINT, fg_color="transparent",
        ).pack(anchor="w", pady=(0, 10))
        return card, inner

    def _option_row(self, master, label: str, values: list[str], current: str, command):
        row = ctk.CTkFrame(master, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            row, text=label, font=theme.font(12), text_color=theme.TEXT,
            width=180, anchor="w", fg_color="transparent",
        ).pack(side="left")
        menu = ctk.CTkOptionMenu(
            row,
            values=values,
            width=240,
            height=32,
            corner_radius=theme.RADIUS_BUTTON,
            fg_color=theme.INPUT_BG,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT,
            dropdown_fg_color=theme.CARD,
            text_color=theme.TEXT,
            font=theme.font(12),
            command=command,
        )
        menu.set(current)
        menu.pack(side="left")
        return menu

    def _build_downloads(self, master) -> None:
        _, inner = self._section(master, "Downloads")
        settings = self.controller.settings

        # Default folder ---------------------------------------------------
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            row, text="Download folder", font=theme.font(12), text_color=theme.TEXT,
            width=180, anchor="w", fg_color="transparent",
        ).pack(side="left")
        self.dir_entry = ctk.CTkEntry(
            row, height=32, corner_radius=theme.RADIUS_BUTTON, fg_color=theme.INPUT_BG,
            border_color=theme.BORDER, text_color=theme.TEXT, font=theme.font(12),
        )
        self.dir_entry.insert(0, settings.download_dir)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.dir_entry.bind("<FocusOut>", lambda _e: self._save_dir())
        small_button(row, "Choose…", self._browse_dir, icon_name="folder", variant="solid").pack(
            side="left"
        )

        self.mode_menu = self._option_row(
            inner, "Default mode", list(_MODE_CHOICES),
            _invert(_MODE_CHOICES).get(settings.default_mode, "Video (with audio)"),
            self._on_mode,
        )
        self.quality_menu = self._option_row(
            inner, "Preferred quality", list(_QUALITY_CHOICES),
            _invert(_QUALITY_CHOICES).get(settings.preferred_quality, "Best available"),
            self._on_quality,
        )
        self.container_menu = self._option_row(
            inner, "Merge container", [c.upper() for c in config.VIDEO_CONTAINERS],
            settings.video_container.upper(),
            self._on_container,
        )
        ctk.CTkLabel(
            inner,
            text="Used when video and audio streams must be merged. MP4 is most "
            "compatible; MKV accepts nearly every codec.",
            font=theme.font(11), text_color=theme.TEXT_FAINT, fg_color="transparent",
            wraplength=560, justify="left",
        ).pack(anchor="w", pady=(0, 8))
        self.concurrency_menu = self._option_row(
            inner, "Parallel downloads", [str(n) for n in config.MAX_CONCURRENT_CHOICES],
            str(settings.max_concurrent),
            self._on_concurrency,
        )

    def _build_appearance(self, master) -> None:
        _, inner = self._section(master, "Appearance")
        settings = self.controller.settings

        self.appearance_menu = self._option_row(
            inner, "Mode", list(_APPEARANCE_CHOICES),
            _invert(_APPEARANCE_CHOICES).get(settings.appearance, "Dark"),
            self._on_appearance,
        )
        self.accent_menu = self._option_row(
            inner, "Accent colour", list(_ACCENT_CHOICES),
            _invert(_ACCENT_CHOICES).get(settings.accent_theme, "Blue"),
            self._on_accent,
        )
        ctk.CTkLabel(
            inner,
            text="Accent colour changes apply the next time the app starts.",
            font=theme.font(11), text_color=theme.TEXT_FAINT, fg_color="transparent",
        ).pack(anchor="w")

    def _build_application(self, master) -> None:
        _, inner = self._section(master, "Application")

        # Update check -----------------------------------------------------
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        self.updates_switch = ctk.CTkSwitch(
            row, text="Check for yt-dlp updates when the app starts",
            font=theme.font(12), text_color=theme.TEXT,
            command=self._on_updates_toggle,
        )
        if self.controller.settings.check_updates_on_start:
            self.updates_switch.select()
        self.updates_switch.pack(side="left")

        version_row = ctk.CTkFrame(inner, fg_color="transparent")
        version_row.pack(fill="x", pady=(4, 8))
        self.ytdlp_label = ctk.CTkLabel(
            version_row, text=f"yt-dlp version: {self.controller.yt_dlp_version}",
            font=theme.font(12), text_color=theme.TEXT_DIM, fg_color="transparent",
        )
        self.ytdlp_label.pack(side="left")
        self.update_result = ctk.CTkLabel(
            version_row, text="", font=theme.font(12), text_color=theme.TEXT_DIM,
            fg_color="transparent",
        )
        self.update_result.pack(side="left", padx=(12, 0))
        small_button(
            version_row, "Check now", self._check_updates, icon_name="retry", variant="solid"
        ).pack(side="right")

        # FFmpeg ------------------------------------------------------------
        ffmpeg_row = ctk.CTkFrame(inner, fg_color="transparent")
        ffmpeg_row.pack(fill="x", pady=(10, 4))
        ctk.CTkLabel(
            ffmpeg_row, text="FFmpeg path", font=theme.font(12), text_color=theme.TEXT,
            width=180, anchor="w", fg_color="transparent",
        ).pack(side="left")
        self.ffmpeg_entry = ctk.CTkEntry(
            ffmpeg_row, height=32, placeholder_text="Leave empty to use PATH",
            corner_radius=theme.RADIUS_BUTTON, fg_color=theme.INPUT_BG,
            border_color=theme.BORDER, text_color=theme.TEXT, font=theme.font(12),
        )
        self.ffmpeg_entry.insert(0, self.controller.settings.ffmpeg_path)
        self.ffmpeg_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.ffmpeg_entry.bind("<FocusOut>", lambda _e: self._save_ffmpeg_path())
        small_button(
            ffmpeg_row, "Browse…", self._browse_ffmpeg, icon_name="folder", variant="solid"
        ).pack(side="left", padx=(0, 6))
        small_button(
            ffmpeg_row, "Detect", self._detect_ffmpeg, icon_name="search", variant="solid"
        ).pack(side="left")

        info = self.controller.ffmpeg_info
        self.ffmpeg_status = ctk.CTkLabel(
            inner, text="", font=theme.font(12), fg_color="transparent", anchor="w",
            justify="left", wraplength=620,
        )
        self.ffmpeg_status.pack(fill="x", pady=(2, 0))
        self._render_ffmpeg_status()

    def _build_danger(self, master) -> None:
        _, inner = self._section(master, "Maintenance")
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")
        small_button(
            row, "Reset all settings to defaults", self._reset_defaults,
            icon_name="alert", variant="danger",
        ).pack(side="left")
        ctk.CTkLabel(
            row,
            text="Downloaded files and history are never touched by this.",
            font=theme.font(11), text_color=theme.TEXT_FAINT, fg_color="transparent",
        ).pack(side="left", padx=(10, 0))

    # ------------------------------------------------------------------ #
    # Save handlers
    # ------------------------------------------------------------------ #

    def _save_dir(self) -> None:
        value = self.dir_entry.get().strip()
        if not value or value == self.controller.settings.download_dir:
            return
        from services.file_service import DestinationError, validate_destination

        try:
            validate_destination(value, create=True)
        except DestinationError as exc:
            dialogs.show_message(self, "Folder not usable", str(exc), level="error")
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, self.controller.settings.download_dir)
            return
        self.controller.save_settings(download_dir=value, announce=True)

    def _browse_dir(self) -> None:
        chosen = dialogs.pick_folder(self, self.controller.settings.download_dir)
        if chosen:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, chosen)
            self._save_dir()

    def _on_mode(self, label: str) -> None:
        self.controller.save_settings(default_mode=_MODE_CHOICES[label], announce=True)

    def _on_quality(self, label: str) -> None:
        self.controller.save_settings(preferred_quality=_QUALITY_CHOICES[label], announce=True)

    def _on_container(self, label: str) -> None:
        self.controller.save_settings(video_container=label.lower(), announce=True)

    def _on_concurrency(self, label: str) -> None:
        self.controller.save_settings(max_concurrent=label, announce=True)

    def _on_appearance(self, label: str) -> None:
        self.controller.save_settings(appearance=label, announce=True)

    def _on_accent(self, label: str) -> None:
        self.controller.save_settings(accent_theme=_ACCENT_CHOICES[label], announce=True)

    def _on_updates_toggle(self) -> None:
        enabled = bool(self.updates_switch.get())
        self.controller.save_settings(
            check_updates_on_start="1" if enabled else "0", announce=True
        )

    def _save_ffmpeg_path(self) -> None:
        value = self.ffmpeg_entry.get().strip()
        if value == self.controller.settings.ffmpeg_path:
            return
        self.controller.save_settings(ffmpeg_path=value, announce=True)
        self._render_ffmpeg_status()

    def _browse_ffmpeg(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(
            title="Locate the ffmpeg executable",
            parent=self,
            filetypes=[("Executables", "ffmpeg*"), ("All files", "*.*")],
        )
        if chosen:
            self.ffmpeg_entry.delete(0, "end")
            self.ffmpeg_entry.insert(0, chosen)
            self._save_ffmpeg_path()

    def _detect_ffmpeg(self) -> None:
        self.controller.redetect_ffmpeg()
        self._save_ffmpeg_path()  # no-op unless the entry changed
        self._render_ffmpeg_status()
        self.controller.toast(
            "FFmpeg detected." if self.controller.ffmpeg_info.available
            else "FFmpeg still not found.",
            "success" if self.controller.ffmpeg_info.available else "warning",
        )

    def _render_ffmpeg_status(self) -> None:
        info = self.controller.ffmpeg_info
        if info.available:
            text = f"✓ {info.label}"
            color = theme.SUCCESS
        else:
            from services.media_service import ffmpeg_install_hint

            text = f"✗ FFmpeg not found. {ffmpeg_install_hint()}."
            color = theme.DANGER
        self.ffmpeg_status.configure(text=text, text_color=color)

    def _check_updates(self) -> None:
        self.update_result.configure(text="Checking…", text_color=theme.TEXT_DIM)
        self.controller.check_yt_dlp_update_now(callback=self._render_update_result)

    def _render_update_result(self, result) -> None:
        if result is None:
            self.update_result.configure(
                text="✓ yt-dlp is up to date", text_color=theme.SUCCESS
            )
        elif isinstance(result, str):
            self.update_result.configure(text=result, text_color=theme.DANGER)
        else:
            installed, latest = result
            self.update_result.configure(
                text=f"Update available: {installed} → {latest} "
                "(update it yourself with: uv pip install -U yt-dlp)",
                text_color=theme.WARNING,
            )

    def _reset_defaults(self) -> None:
        if dialogs.confirm(
            self,
            "Reset settings?",
            "All preferences return to their defaults. "
            "Downloaded files and history are not affected.",
            confirm_label="Reset",
            destructive=True,
        ):
            self.controller.reset_settings()
            self.reload()

    def reload(self) -> None:
        """Re-populate all widgets from the current settings."""
        settings = self.controller.settings
        self.dir_entry.delete(0, "end")
        self.dir_entry.insert(0, settings.download_dir)
        self.mode_menu.set(_invert(_MODE_CHOICES).get(settings.default_mode, "Video (with audio)"))
        self.quality_menu.set(
            _invert(_QUALITY_CHOICES).get(settings.preferred_quality, "Best available")
        )
        self.container_menu.set(settings.video_container.upper())
        self.concurrency_menu.set(str(settings.max_concurrent))
        self.appearance_menu.set(_invert(_APPEARANCE_CHOICES).get(settings.appearance, "Dark"))
        self.accent_menu.set(_invert(_ACCENT_CHOICES).get(settings.accent_theme, "Blue"))
        if settings.check_updates_on_start:
            self.updates_switch.select()
        else:
            self.updates_switch.deselect()
        self.ffmpeg_entry.delete(0, "end")
        self.ffmpeg_entry.insert(0, settings.ffmpeg_path)
        self._render_ffmpeg_status()
