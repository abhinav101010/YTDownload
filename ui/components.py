"""Reusable UI building blocks shared by all pages."""

from __future__ import annotations

import logging
from typing import Callable, Optional

import customtkinter as ctk

from ui import icons, theme

logger = logging.getLogger(__name__)

NAV_ITEMS = (
    ("home", "Home", "home"),
    ("downloads", "Downloads", "download"),
    ("history", "History", "history"),
    ("settings", "Settings", "settings"),
)


class Card(ctk.CTkFrame):
    """Standard rounded content card."""

    def __init__(self, master, **kwargs) -> None:
        kwargs.setdefault("fg_color", theme.CARD)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.BORDER)
        kwargs.setdefault("corner_radius", theme.RADIUS_CARD)
        super().__init__(master, **kwargs)


class Sidebar(ctk.CTkFrame):
    """Left navigation rail with logo, page buttons and appearance toggle."""

    def __init__(
        self,
        master,
        on_navigate: Callable[[str], None],
        on_toggle_appearance: Callable[[], None],
    ) -> None:
        super().__init__(
            master,
            width=theme.SIDEBAR_WIDTH,
            corner_radius=0,
            fg_color=theme.SIDEBAR_BG,
            border_width=0,
        )
        self.pack_propagate(False)
        self._on_navigate = on_navigate
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._indicators: dict[str, ctk.CTkFrame] = {}

        # Logo + app name --------------------------------------------------
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(22, 6))
        ctk.CTkLabel(header, text="", image=icons.logo(34)).pack(side="left")
        ctk.CTkLabel(
            header,
            text="YT Downloader",
            font=theme.font(16, "bold"),
            text_color=theme.TEXT,
            fg_color="transparent",
        ).pack(side="left", padx=(10, 0))

        ctk.CTkLabel(
            self,
            text="Local media downloader",
            font=theme.font(11),
            text_color=theme.TEXT_FAINT,
            fg_color="transparent",
        ).pack(anchor="w", padx=22, pady=(0, 16))

        # Navigation -------------------------------------------------------
        for key, label, icon_name in NAV_ITEMS:
            self._add_nav_button(key, label, icon_name)

        # Footer -----------------------------------------------------------
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=14, pady=16)

        self.appearance_button = ctk.CTkButton(
            footer,
            text="Dark mode",
            image=icons.icon("settings", 16),
            compound="left",
            anchor="w",
            height=36,
            corner_radius=theme.RADIUS_BUTTON,
            fg_color="transparent",
            hover_color=theme.CARD_HOVER,
            text_color=theme.TEXT_DIM,
            font=theme.font(12),
            command=on_toggle_appearance,
        )
        self.appearance_button.pack(fill="x")

        ctk.CTkLabel(
            footer,
            text="v1.0.0 · use responsibly",
            font=theme.font(10),
            text_color=theme.TEXT_FAINT,
            fg_color="transparent",
        ).pack(anchor="w", padx=6, pady=(8, 0))

    def _add_nav_button(self, key: str, label: str, icon_name: str) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent", height=42)
        row.pack(fill="x", padx=14, pady=3)
        row.pack_propagate(False)

        indicator = ctk.CTkFrame(row, width=4, fg_color=theme.ACCENT, corner_radius=2)
        indicator.pack(side="left", fill="y", padx=(0, 8))
        indicator.pack_forget()
        self._indicators[key] = indicator

        button = ctk.CTkButton(
            row,
            text=label,
            image=icons.icon(icon_name, 19),
            compound="left",
            anchor="w",
            height=40,
            corner_radius=theme.RADIUS_BUTTON,
            fg_color="transparent",
            hover_color=theme.CARD_HOVER,
            text_color=theme.TEXT_DIM,
            font=theme.font(13),
            command=lambda k=key: self._on_navigate(k),
        )
        button.pack(fill="both", expand=True)
        self._buttons[key] = button

    def set_selected(self, key: str) -> None:
        for name, button in self._buttons.items():
            selected = name == key
            button.configure(
                fg_color=theme.CARD if selected else "transparent",
                text_color=theme.TEXT if selected else theme.TEXT_DIM,
                font=theme.font(13, "bold" if selected else "normal"),
            )
            indicator = self._indicators[name]
            if selected:
                indicator.pack(side="left", fill="y", padx=(0, 8))
            else:
                indicator.pack_forget()

    def set_appearance_label(self, is_dark: bool) -> None:
        self.appearance_button.configure(text="Dark mode" if is_dark else "Light mode")


class PageHeader(ctk.CTkFrame):
    """Title + optional subtitle on the left, actions on the right."""

    def __init__(self, master, title: str, subtitle: str = "") -> None:
        super().__init__(master, fg_color="transparent")
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            left, text=title, font=theme.font(25, "bold"), text_color=theme.TEXT,
            fg_color="transparent",
        ).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                left, text=subtitle, font=theme.font(12), text_color=theme.TEXT_DIM,
                fg_color="transparent",
            ).pack(anchor="w", pady=(2, 0))
        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.pack(side="right", padx=(12, 0))


class EmptyState(ctk.CTkFrame):
    """Attractive placeholder shown when a list has nothing in it."""

    def __init__(
        self, master, icon_name: str, title: str, message: str, *, compact: bool = False
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._icon_name = icon_name
        self._title = title
        self._message = message
        self._compact = compact
        self._built = False
        self.build()

    def build(self) -> None:
        if self._built:
            return
        self._built = True
        size = 46 if self._compact else 60
        ctk.CTkLabel(self, text="", image=icons.icon(self._icon_name, size)).pack(
            pady=(10 if self._compact else 24, 8)
        )
        self._title_label = ctk.CTkLabel(
            self,
            text=self._title,
            font=theme.font(15 if self._compact else 17, "bold"),
            text_color=theme.TEXT_DIM,
            fg_color="transparent",
        )
        self._title_label.pack()
        self._message_label = ctk.CTkLabel(
            self,
            text=self._message,
            font=theme.font(12),
            text_color=theme.TEXT_FAINT,
            fg_color="transparent",
            wraplength=420,
            justify="center",
        )
        self._message_label.pack(pady=(4, 16 if not self._compact else 8))

    def set_text(self, title: str, message: str) -> None:
        self._title_label.configure(text=title)
        self._message_label.configure(text=message)


class StatusBadge(ctk.CTkLabel):
    """Small pill showing a download/history status."""

    def __init__(self, master, status: str = "queued") -> None:
        super().__init__(
            master,
            text="",
            corner_radius=999,
            height=22,
            padx=9,
            font=theme.font(11, "bold"),
            fg_color="transparent",
        )
        self.set_status(status)

    def set_status(self, status: str) -> None:
        color = theme.status_color(status)
        pill = theme.STATUS_PILL.get(status, theme.STATUS_PILL["queued"])
        self.configure(
            text=theme.status_text(status).upper(),
            text_color=color,
            fg_color=pill,
        )


def small_button(
    master,
    text: str,
    command: Optional[Callable[[], None]] = None,
    *,
    icon_name: Optional[str] = None,
    variant: str = "default",
    width: int = 0,
) -> ctk.CTkButton:
    """Compact action button used inside rows."""
    colors = {
        "default": ("transparent", theme.TEXT_DIM),
        "primary": (theme.ACCENT, "#ffffff"),
        "danger": (theme.DANGER_SOFT, theme.DANGER),
        "solid": (theme.CARD_HOVER, theme.TEXT),
    }
    fg, text_color = colors.get(variant, colors["default"])
    hover = theme.BORDER if variant in ("default", "solid") else fg
    return ctk.CTkButton(
        master,
        text=text,
        image=icons.icon(icon_name, 15) if icon_name else None,
        compound="left",
        command=command,
        height=28,
        width=width or (10 + 9 * len(text) + (24 if icon_name else 0)),
        corner_radius=theme.RADIUS_BUTTON,
        fg_color=fg,
        hover_color=hover,
        text_color=text_color,
        font=theme.font(12),
        anchor="center",
    )


class ToastManager:
    """Transient status messages at the bottom-right of the window."""

    def __init__(self, master) -> None:
        self._master = master
        self._frame: Optional[ctk.CTkFrame] = None
        self._job: Optional[str] = None

    def show(self, message: str, level: str = "info", duration_ms: int = 3600) -> None:
        color = {
            "success": theme.SUCCESS,
            "error": theme.DANGER,
            "warning": theme.WARNING,
        }.get(level, theme.ACCENT)
        icon_name = {
            "success": "check",
            "error": "alert",
            "warning": "alert",
        }.get(level, "info")

        self._dismiss()
        frame = ctk.CTkFrame(
            self._master,
            fg_color=theme.CARD,
            border_width=1,
            border_color=color[1],
            corner_radius=theme.RADIUS_CARD,
        )
        ctk.CTkLabel(frame, text="", image=icons.icon(icon_name, 18)).pack(
            side="left", padx=(12, 6), pady=10
        )
        ctk.CTkLabel(
            frame,
            text=message,
            font=theme.font(12),
            text_color=theme.TEXT,
            wraplength=360,
            justify="left",
        ).pack(side="left", padx=(0, 14), pady=10)
        frame.place(relx=1.0, rely=1.0, anchor="se", x=-22, y=-18)
        self._frame = frame
        self._job = frame.after(duration_ms, self._dismiss)

    def _dismiss(self) -> None:
        if self._job is not None and self._frame is not None:
            try:
                self._frame.after_cancel(self._job)
            except Exception:
                pass
        self._job = None
        if self._frame is not None:
            try:
                self._frame.destroy()
            except Exception:
                pass
        self._frame = None
