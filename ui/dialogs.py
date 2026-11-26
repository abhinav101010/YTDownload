"""Modal dialogs: confirmation, message boxes and the folder picker."""

from __future__ import annotations

from typing import Optional

import customtkinter as ctk
from tkinter import filedialog

from ui import theme


class _ModalDialog(ctk.CTkToplevel):
    """Small modal window returning a result when closed."""

    def __init__(
        self,
        master,
        title: str,
        message: str,
        buttons: list[tuple[str, str]],
        *,
        icon_name: Optional[str] = None,
        primary_key: str = "ok",
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.result: Optional[str] = None
        self._buttons = buttons
        self._primary_key = primary_key

        self.configure(fg_color=theme.PAGE_BG)

        container = ctk.CTkFrame(
            self,
            fg_color=theme.CARD,
            border_width=1,
            border_color=theme.BORDER,
            corner_radius=theme.RADIUS_CARD,
        )
        container.pack(fill="both", expand=True, padx=2, pady=2)

        header = ctk.CTkFrame(container, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 4))
        if icon_name:
            from ui import icons

            ctk.CTkLabel(header, text="", image=icons.icon(icon_name, 22),
                         fg_color="transparent").pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            header, text=title, font=theme.font(16, "bold"), text_color=theme.TEXT,
            fg_color="transparent",
        ).pack(side="left")

        ctk.CTkLabel(
            container,
            text=message,
            font=theme.font(13),
            text_color=theme.TEXT_DIM,
            wraplength=400,
            justify="left",
            fg_color="transparent",
        ).pack(anchor="w", padx=20, pady=(6, 14))

        row = ctk.CTkFrame(container, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 18))
        for key, label in reversed(buttons):  # primary ends up rightmost
            is_primary = key == primary_key
            button = ctk.CTkButton(
                row,
                text=label,
                width=100,
                height=34,
                corner_radius=theme.RADIUS_BUTTON,
                fg_color=theme.ACCENT if is_primary else theme.CARD_HOVER,
                hover_color=theme.ACCENT if is_primary else theme.BORDER,
                text_color="#ffffff" if is_primary else theme.TEXT,
                font=theme.font(13),
                command=lambda k=key: self._choose(k),
            )
            button.pack(side="right", padx=6)

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())
        self.bind("<Return>", lambda _e: self._choose(buttons[0][0]))

    def _choose(self, key: str) -> None:
        self.result = key
        self.destroy()

    def _close(self) -> None:
        self.result = None
        self.destroy()

    def run(self) -> Optional[str]:
        self.grab_set()
        self._center()
        self.wait_window()
        return self.result

    def _center(self) -> None:
        self.update_idletasks()
        parent = self.master.winfo_toplevel()
        px = parent.winfo_rootx() + parent.winfo_width() // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{max(0, px - w // 2)}+{max(0, py - h // 2)}")


def confirm(
    parent,
    title: str,
    message: str,
    *,
    confirm_label: str = "Confirm",
    cancel_label: str = "Cancel",
    destructive: bool = False,  # noqa: ARG001 - reserved for future styling
) -> bool:
    """Ask a yes/no question. Returns True only for an explicit confirm."""
    dialog = _ModalDialog(
        parent,
        title,
        message,
        [("yes", confirm_label), ("no", cancel_label)],
        primary_key="yes",
    )
    return dialog.run() == "yes"


def show_message(parent, title: str, message: str, *, level: str = "info") -> None:
    icon_name = {"error": "alert", "warning": "alert", "success": "check"}.get(level, "info")
    _ModalDialog(parent, title, message, [("ok", "OK")], icon_name=icon_name).run()


def pick_folder(parent, initial: str = "") -> Optional[str]:
    """Native folder chooser. Returns None when cancelled."""
    options: dict = {"title": "Choose download folder", "mustexist": True, "parent": parent}
    if initial:
        options["initialdir"] = initial
    chosen = filedialog.askdirectory(**options)
    return chosen or None
