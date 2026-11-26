"""Home page: URL entry, video information and download options."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from downloader.formats import (
    QualityOption,
    build_audio_options,
    build_video_options,
    option_detail,
    pick_option,
)
from downloader.metadata import MediaInfo
from downloader.engine import DownloadRequest
from services.file_service import DestinationError, validate_destination
from ui import dialogs, icons, theme
from ui.components import Card, EmptyState, PageHeader, small_button

if TYPE_CHECKING:  # pragma: no cover
    from app.application import YTDownloaderApp

logger = logging.getLogger(__name__)

THUMB_W, THUMB_H = 232, 130


class HomePage(ctk.CTkFrame):
    """Paste a URL → inspect metadata → pick options → queue a download."""

    def __init__(self, master, controller: "YTDownloaderApp") -> None:
        super().__init__(master, fg_color="transparent")
        self.controller = controller
        self._info: Optional[MediaInfo] = None
        self._video_options: list[QualityOption] = []
        self._audio_options: list[QualityOption] = []
        self._menu_labels: list[str] = []
        self._destination: Path = Path(controller.settings.download_dir)
        self._fetching = False
        self._thumb_url: Optional[str] = None
        self._thumb_ctk: Optional[ctk.CTkImage] = None

        self._build()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        PageHeader(
            self, "Home", "Paste a link to a video you are allowed to download."
        ).pack(fill="x", padx=theme.PAGE_PAD, pady=(theme.PAGE_PAD, 14))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True, padx=(theme.PAGE_PAD - 6, theme.PAGE_PAD - 6))
        self._body = body

        self._build_url_card(body)
        self._build_info_area(body)
        self._build_options_card(body)

    def _build_url_card(self, master) -> None:
        card = Card(master)
        card.pack(fill="x", padx=6, pady=(0, 14))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)

        ctk.CTkLabel(
            inner, text="VIDEO URL", font=theme.font(11, "bold"),
            text_color=theme.TEXT_FAINT, fg_color="transparent",
        ).pack(anchor="w", pady=(0, 6))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")

        self.url_entry = ctk.CTkEntry(
            row,
            placeholder_text="https://www.youtube.com/watch?v=…",
            height=40,
            corner_radius=theme.RADIUS_BUTTON,
            fg_color=theme.INPUT_BG,
            border_color=theme.BORDER,
            text_color=theme.TEXT,
            font=theme.font(13),
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _e: self._on_fetch())

        self.paste_button = small_button(
            row, "Paste", self._paste, icon_name="paste", variant="solid"
        )
        self.paste_button.pack(side="left", padx=(0, 6))

        self.clear_button = small_button(
            row, "Clear", self._clear, icon_name="clear", variant="solid"
        )
        self.clear_button.pack(side="left", padx=(0, 10))

        self.fetch_button = ctk.CTkButton(
            row,
            text="Fetch Information",
            height=40,
            width=158,
            corner_radius=theme.RADIUS_BUTTON,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT,
            text_color="#ffffff",
            font=theme.font(13, "bold"),
            command=self._on_fetch,
        )
        self.fetch_button.pack(side="left")

        self.url_error = ctk.CTkLabel(
            inner, text="", font=theme.font(12), text_color=theme.DANGER,
            fg_color="transparent", anchor="w",
        )

        loading = ctk.CTkFrame(inner, fg_color="transparent")
        self._loading_bar = ctk.CTkProgressBar(loading, width=140, height=12, mode="indeterminate")
        self._loading_bar.pack(side="left", padx=(0, 10))
        self._loading_label = ctk.CTkLabel(
            loading, text="Fetching video information…", font=theme.font(12),
            text_color=theme.TEXT_DIM, fg_color="transparent",
        )
        self._loading_label.pack(side="left")
        self._loading_row = loading  # pack/unpack as one unit

    def _build_info_area(self, master) -> None:
        self._empty_state = EmptyState(
            master,
            "link",
            "No video loaded yet",
            "Paste a video URL above and press Fetch Information. "
            "Formats and qualities are read from the source — nothing is downloaded yet.",
        )
        self._empty_state.pack(fill="x", padx=6, pady=(0, 14))

        self._info_card = Card(master)
        inner = ctk.CTkFrame(self._info_card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)

        self._thumb_label = ctk.CTkLabel(
            inner, text="", width=THUMB_W, height=THUMB_H, corner_radius=10,
            fg_color=theme.INPUT_BG, image=icons.placeholder(46),
            compound="center",
        )
        self._thumb_label.pack(side="left", anchor="n")

        details = ctk.CTkFrame(inner, fg_color="transparent")
        details.pack(side="left", fill="both", expand=True, padx=(16, 0))

        self._title_label = ctk.CTkLabel(
            details, text="", font=theme.font(16, "bold"), text_color=theme.TEXT,
            fg_color="transparent", anchor="w", justify="left",
        )
        self._title_label.pack(fill="x")

        self._meta_label = ctk.CTkLabel(
            details, text="", font=theme.font(12), text_color=theme.TEXT_DIM,
            fg_color="transparent", anchor="w", justify="left",
        )
        self._meta_label.pack(fill="x", pady=(4, 0))

        self._live_warning = ctk.CTkLabel(
            details,
            text="⚠ This is a live stream — it can't be downloaded.",
            font=theme.font(12, "bold"),
            text_color=theme.WARNING,
            fg_color="transparent",
            anchor="w",
        )

        details.bind("<Configure>", lambda _e: self._update_wraplengths())
        self._info_card_inner = inner

    def _build_options_card(self, master) -> None:
        card = Card(master)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        self._options_card = card
        self._options_inner = inner

        ctk.CTkLabel(
            inner, text="DOWNLOAD OPTIONS", font=theme.font(11, "bold"),
            text_color=theme.TEXT_FAINT, fg_color="transparent",
        ).pack(anchor="w", pady=(0, 10))

        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            mode_row, text="Mode", font=theme.font(12), text_color=theme.TEXT_DIM,
            width=64, anchor="w", fg_color="transparent",
        ).pack(side="left")
        self.mode_switch = ctk.CTkSegmentedButton(
            mode_row, values=["Video", "Audio"], width=220,
            command=self._on_mode_change, font=theme.font(12, "bold"),
        )
        self.mode_switch.set("Video")
        self.mode_switch.pack(side="left")

        quality_row = ctk.CTkFrame(inner, fg_color="transparent")
        quality_row.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            quality_row, text="Quality", font=theme.font(12), text_color=theme.TEXT_DIM,
            width=64, anchor="w", fg_color="transparent",
        ).pack(side="left")
        self.quality_menu = ctk.CTkOptionMenu(
            quality_row,
            values=["—"],
            width=300,
            height=32,
            corner_radius=theme.RADIUS_BUTTON,
            fg_color=theme.INPUT_BG,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT,
            dropdown_fg_color=theme.CARD,
            dropdown_hover_color=theme.CARD_HOVER,
            text_color=theme.TEXT,
            font=theme.font(12),
            command=self._on_quality_change,
        )
        self.quality_menu.pack(side="left", padx=(0, 10))

        self.quality_note = ctk.CTkLabel(
            inner, text="", font=theme.font(11), text_color=theme.TEXT_FAINT,
            fg_color="transparent", anchor="w", justify="left", wraplength=560,
        )
        self.quality_note.pack(fill="x", pady=(0, 10))

        dest_row = ctk.CTkFrame(inner, fg_color="transparent")
        dest_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            dest_row, text="Save to", font=theme.font(12), text_color=theme.TEXT_DIM,
            width=64, anchor="w", fg_color="transparent",
        ).pack(side="left")
        self.dest_label = ctk.CTkLabel(
            dest_row, text=str(self._destination), font=theme.font(12),
            text_color=theme.TEXT, fg_color="transparent", anchor="w",
        )
        self.dest_label.pack(side="left", fill="x", expand=True)
        small_button(
            dest_row, "Change…", self._change_destination, icon_name="folder", variant="solid"
        ).pack(side="left")

        self.ffmpeg_note = ctk.CTkLabel(
            inner, text="", font=theme.font(12), text_color=theme.DANGER,
            fg_color="transparent", anchor="w", justify="left", wraplength=560,
        )

        bottom = ctk.CTkFrame(inner, fg_color="transparent")
        bottom.pack(fill="x", pady=(4, 0))
        self.download_hint = ctk.CTkLabel(
            bottom,
            text="Downloads run in the background — watch them on the Downloads page.",
            font=theme.font(11),
            text_color=theme.TEXT_FAINT,
            fg_color="transparent",
        )
        self.download_hint.pack(side="left")
        self.download_button = ctk.CTkButton(
            bottom,
            text="Download",
            height=38,
            width=190,
            corner_radius=theme.RADIUS_BUTTON,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT,
            text_color="#ffffff",
            font=theme.font(13, "bold"),
            command=self._on_download,
        )
        self.download_button.pack(side="right")

        # Hidden until metadata arrives.
        self._empty_state.pack_forget()
        self._info_card.pack_forget()
        card.pack_forget()
        self._options_packed = False

    # ------------------------------------------------------------------ #
    # Public API (called by the controller)
    # ------------------------------------------------------------------ #

    def set_loading(self, loading: bool, message: str = "Fetching video information…") -> None:
        self._fetching = loading
        self.fetch_button.configure(state="disabled" if loading else "normal",
                                    text="Fetching…" if loading else "Fetch Information")
        self._loading_label.configure(text=message)
        if loading:
            self._loading_row.pack(fill="x", pady=(10, 0))
            self._loading_bar.start()
        else:
            self._loading_bar.stop()
            self._loading_row.pack_forget()
        self._hide_url_error()

    def show_url_error(self, message: str) -> None:
        self.url_error.configure(text=message)
        self.url_error.pack(fill="x", pady=(6, 0))

    def _hide_url_error(self) -> None:
        self.url_error.pack_forget()

    def show_metadata(self, info: MediaInfo) -> None:
        """Populate the information and options panels."""
        self._info = info
        self._video_options = build_video_options(
            info.formats,
            duration=info.duration,
            container=self.controller.settings.video_container,
        )
        self._audio_options = build_audio_options(
            info.formats, duration=info.duration, allow_mp3=True
        )

        self._title_label.configure(text=info.title or "Untitled video")
        from downloader.progress import format_duration

        meta_parts = []
        if info.uploader:
            meta_parts.append(info.uploader)
        if info.duration:
            meta_parts.append(format_duration(info.duration))
        if info.extractor:
            meta_parts.append(info.extractor)
        self._meta_label.configure(text="  ·  ".join(meta_parts) or "Details unavailable")

        self._thumb_url = info.thumbnail_url
        self._thumb_ctk = None
        self._thumb_label.configure(image=icons.placeholder(46), text="")

        if info.is_live:
            self._live_warning.pack(fill="x", pady=(6, 0))
            self.download_button.configure(state="disabled")
        else:
            self._live_warning.pack_forget()

        self._empty_state.pack_forget()
        self._info_card.pack(fill="x", padx=6, pady=(0, 14))
        if not self._options_packed:
            self._options_card.pack(fill="x", padx=6, pady=(0, 14))
            self._options_packed = True

        self.mode_switch.set("Video" if self.controller.settings.default_mode == "video" else "Audio")
        self._rebuild_quality_menu()
        self._update_wraplengths()

        if info.thumbnail_url:
            self.controller.request_thumbnail(info.thumbnail_url)

    def show_thumbnail(self, url: str, image) -> None:
        """Apply a thumbnail fetched in the background (still current?)."""
        if url != self._thumb_url or image is None:
            return
        try:
            from PIL import Image

            ratio = min(THUMB_W / image.width, THUMB_H / image.height)
            size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
            resized = image.resize(size, Image.LANCZOS)
            self._thumb_ctk = ctk.CTkImage(
                dark_image=resized, light_image=resized, size=size
            )
            self._thumb_label.configure(image=self._thumb_ctk, text="")
        except Exception:
            logger.debug("Could not display thumbnail", exc_info=True)

    def clear_all(self) -> None:
        """Reset the page (used by the Clear button)."""
        self._info = None
        self._video_options = []
        self._audio_options = []
        self._thumb_url = None
        self.url_entry.delete(0, "end")
        self._hide_url_error()
        self._info_card.pack_forget()
        self._options_card.pack_forget()
        self._options_packed = False
        self._empty_state.pack(fill="x", padx=6, pady=(0, 14))
        self._live_warning.pack_forget()

    def refresh_availability(self) -> None:
        """Re-check FFmpeg-dependent options (called after settings change)."""
        if self._info is not None:
            self._on_quality_change(self.quality_menu.get())

    def focus_url(self) -> None:
        self.url_entry.focus_set()

    # ------------------------------------------------------------------ #
    # Internal handlers
    # ------------------------------------------------------------------ #

    def _current_options(self) -> list[QualityOption]:
        return self._audio_options if self._mode() == "audio" else self._video_options

    def _mode(self) -> str:
        return "audio" if self.mode_switch.get() == "Audio" else "video"

    def _on_fetch(self) -> None:
        if self._fetching:
            return
        self.controller.fetch_metadata(self.url_entry.get())

    def _paste(self) -> None:
        try:
            text = self.clipboard_get()
        except Exception:
            self.show_url_error("The clipboard is empty.")
            return
        if text:
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, text.strip())
            self._hide_url_error()

    def _clear(self) -> None:
        self.clear_all()
        self.url_entry.focus_set()

    def _on_mode_change(self, _value: str) -> None:
        self._rebuild_quality_menu()

    def _rebuild_quality_menu(self) -> None:
        options = self._current_options()
        self._menu_labels = [option.label for option in options]
        if not options:
            self.quality_menu.configure(values=["—"], state="disabled")
            self.download_button.configure(state="disabled")
            self.quality_note.configure(
                text="No downloadable formats were reported for this video."
            )
            return
        self.quality_menu.configure(values=self._menu_labels, state="normal")
        _option, matched = pick_option(options, self.controller.settings.preferred_quality)
        selected = _option if _option is not None else options[0]
        self.quality_menu.set(selected.label)
        self._on_quality_change(selected.label)
        if not matched and self.controller.settings.preferred_quality:
            self.download_hint.configure(
                text="Preferred quality not available for this video — showing what it offers."
            )
        else:
            self.download_hint.configure(
                text="Downloads run in the background — watch them on the Downloads page."
            )

    def _selected_option(self) -> Optional[QualityOption]:
        label = self.quality_menu.get()
        for option in self._current_options():
            if option.label == label:
                return option
        return None

    def _on_quality_change(self, label: str) -> None:
        option = self._selected_option()
        note = option_detail(option)
        ffmpeg_ok = self.controller.ffmpeg_info.available

        needs_ffmpeg = bool(option and option.requires_ffmpeg and not ffmpeg_ok)
        if needs_ffmpeg:
            from services.media_service import ffmpeg_install_hint

            note = f"{note}  ·  FFmpeg required — {ffmpeg_install_hint()}"
            self.download_button.configure(state="disabled", text="FFmpeg required")
        elif self._info and self._info.is_live:
            self.download_button.configure(state="disabled", text="Unavailable")
        else:
            self.download_button.configure(state="normal", text="Download")

        self.quality_note.configure(text=note)
        if needs_ffmpeg:
            self.ffmpeg_note.configure(
                text="⚠ FFmpeg wasn't found, so options that need merging or "
                "conversion are unavailable."
            )
            self.ffmpeg_note.pack(fill="x", pady=(0, 6))
        else:
            self.ffmpeg_note.pack_forget()

    def _change_destination(self) -> None:
        chosen = dialogs.pick_folder(self, str(self._destination))
        if not chosen:
            return
        try:
            validate_destination(chosen, create=False)
        except DestinationError as exc:
            dialogs.show_message(self, "Folder not usable", str(exc), level="error")
            return
        self._destination = Path(chosen)
        self.dest_label.configure(text=str(self._destination))

    def _on_download(self) -> None:
        if self._info is None:
            return
        option = self._selected_option()
        if option is None:
            self.show_url_error("Choose a quality first.")
            return

        ffmpeg_info = self.controller.ffmpeg_info
        request = self._build_request(option, ffmpeg_info)
        if request is None:
            return

        accepted, message = self.controller.queue_download(request)
        if accepted:
            self.controller.toast(f"Queued: {self._info.title}", "success")
        else:
            self.controller.toast(message, "warning")

    def _build_request(
        self, option: QualityOption, ffmpeg_info
    ) -> Optional[DownloadRequest]:
        info = self._info
        assert info is not None
        try:
            destination = validate_destination(self._destination, create=True)
        except DestinationError as exc:
            dialogs.show_message(self, "Folder not usable", str(exc), level="error")
            return None

        mode = self._mode()
        if mode == "video":
            format_label = (
                f"Merged → {option.container.upper()}"
                if option.requires_merge and option.container
                else "Source container"
            )
            container = option.container
            audio_ext = None
            convert_audio = False
        else:
            format_label = "MP3" if option.convert_audio else (option.audio_ext or "audio").upper()
            container = None
            audio_ext = option.audio_ext
            convert_audio = option.convert_audio

        return DownloadRequest(
            url=info.webpage_url or info.url,
            title=info.title,
            destination=destination,
            mode=mode,
            format_selector=option.format_selector,
            quality_label=option.label,
            format_label=format_label,
            video_id=info.video_id,
            container=container,
            audio_ext=audio_ext,
            convert_audio=convert_audio,
            requires_ffmpeg=option.requires_ffmpeg,
            ffmpeg_path=ffmpeg_info.path if ffmpeg_info.available else None,
            thumbnail_url=info.thumbnail_url,
            estimated_size=option.size_bytes,
        )

    def _update_wraplengths(self) -> None:
        try:
            width = max(320, self._info_card_inner.winfo_width() - 260)
        except Exception:
            width = 480
        self._title_label.configure(wraplength=width)
        self._meta_label.configure(wraplength=width)
