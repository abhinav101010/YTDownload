#!/usr/bin/env python3
"""YT Downloader — application entry point.

Run with:

    python main.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def configure_tcl_tk() -> None:
    """Point Tcl/Tk at the bundled library folders when needed.

    Some standalone CPython builds (e.g. uv/python-build-standalone) ship
    Tcl/Tk *script* libraries next to the base standard library, but Tcl's
    compiled-in lookup misses them once the interpreter runs inside a
    virtual environment. Setting ``TCL_LIBRARY``/``TK_LIBRARY`` fixes that
    without requiring a system-wide Tcl install.
    """
    base = Path(sys.base_prefix)
    for variable, folder, marker in (
        ("TCL_LIBRARY", "tcl8.6", "init.tcl"),
        ("TK_LIBRARY", "tk8.6", "tk.tcl"),
    ):
        if os.environ.get(variable):
            continue
        candidate = base / "lib" / folder
        if (candidate / marker).exists():
            os.environ[variable] = str(candidate)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yt-downloader",
        description="YT Downloader — a local desktop app for downloading media "
        "from supported URLs you are authorized to download.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="show detailed logs on the console"
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: launch, render, print SMOKE OK, exit
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_tcl_tk()

    from app import config

    config.setup_logging(verbose=args.verbose)
    log = logging.getLogger("main")

    try:
        from app.application import YTDownloaderApp
    except ImportError as exc:
        print(
            "Could not load the UI. Make sure dependencies are installed:\n"
            "    pip install -r requirements.txt\n"
            f"Details: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        app = YTDownloaderApp(smoke_test=args.smoke_test)
    except Exception:
        log.exception("The application failed to start")
        print(
            "The application failed to start. See the log for details:\n"
            f"    {config.log_file_path()}",
            file=sys.stderr,
        )
        return 1

    try:
        app.mainloop()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        log.info("Interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
