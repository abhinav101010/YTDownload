# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for YT Downloader.

Build (with PyInstaller installed; paths are resolved from this spec, so the
working directory doesn't matter):

    .venv/bin/python -m pip install pyinstaller
    .venv/bin/python -m PyInstaller packaging/yt_downloader.spec --noconfirm

Outputs:
    macOS  -> dist/YT Downloader.app
    others -> dist/YT Downloader/ (a folder with the executable)

Notes
-----
* ``customtkinter`` ships theme JSON + fonts as package data, so it is
  collected explicitly.
* ``yt_dlp`` resolves extractors dynamically, so its submodules are collected
  too (this is what makes "every site yt-dlp supports" work in the bundle).
* Tk/Tcl libraries are collected by PyInstaller's own ``tkinter`` hook. If the
  bundled app starts without a window, see docs/PACKAGING.md ("Tcl/Tk").
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _point_tcl_tk() -> None:
    """Make Tcl/Tk discoverable to PyInstaller's tkinter hook.

    Some standalone CPython builds (e.g. uv/python-build-standalone) ship the
    Tcl/Tk *script* libraries inside the base prefix, but Tcl's compiled-in
    lookup misses them. PyInstaller probes ``tkinter.Tcl()`` to decide whether
    the tkinter installation is usable and quietly **excludes tkinter** when the
    probe fails, producing a bundle that dies with "No module named 'tkinter'".
    ``main.py`` applies the same fix at runtime; here it is needed at build time.
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


_point_tcl_tk()

# PyInstaller resolves relative paths against the spec's own folder, so derive
# the project root from SPECPATH instead of relying on the working directory.
ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "assets"), "assets"),   # icon PNGs used by the UI
    (str(ROOT / "README.md"), "."),
]
datas += collect_data_files("customtkinter")

hiddenimports = collect_submodules("yt_dlp")
hiddenimports += [
    "PIL._tkinter_finder",
    "darkdetect",
    "packaging",
    "packaging.version",
    "packaging.requirements",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YT Downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="YT Downloader",
)

app = BUNDLE(
    coll,
    name="YT Downloader.app",
    icon=str(ROOT / "assets" / "icons" / "logo.png"),
    bundle_identifier="local.ytdownloader.app",
    info_plist={
        "CFBundleName": "YT Downloader",
        "CFBundleDisplayName": "YT Downloader",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
        # Only needed if you later add network-sandboxed helper features:
        # "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
    },
)
