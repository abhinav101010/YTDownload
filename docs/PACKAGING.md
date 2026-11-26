# Packaging YT Downloader

This document describes how to build a standalone app bundle that runs without a
Python installation on the target machine. The spec file
[`packaging/yt_downloader.spec`](../packaging/yt_downloader.spec) is ready to use.

> Building is intentionally **not** part of the test suite: it downloads
> PyInstaller, analyses the whole dependency tree and produces a large bundle.
> Nothing in the app requires it — it's for distribution only.

---

## 1. Prerequisites

- The app must already run from source in this checkout
  (`.venv/bin/python main.py`).
- PyInstaller (6.x or newer):

```bash
.venv/bin/python -m pip install --upgrade pyinstaller
.venv/bin/python -m PyInstaller --version
```

- Optional, for a polished macOS build: full Xcode command line tools
  (`xcode-select --install`) so `codesign` is available.

You do **not** need to set `TCL_LIBRARY`/`TK_LIBRARY` yourself — the spec sets
them for the build if needed (see "Tcl/Tk" below).

## 2. Build

From the project root:

```bash
.venv/bin/python -m PyInstaller packaging/yt_downloader.spec --noconfirm --clean
```

Outputs:

| Platform | Artifact |
| --- | --- |
| macOS | `dist/YT Downloader.app` |
| Windows / Linux | `dist/YT Downloader/` (folder containing the executable `YT Downloader`) |

## 3. Verify the bundle

Don't ship a bundle you haven't run. Check that it starts, renders, and can
actually download:

```bash
# macOS
open "dist/YT Downloader.app"

# Windows / Linux
"dist/YT Downloader/YT Downloader"
```

Then confirm, inside the app:

1. **Settings** shows the yt-dlp version and the FFmpeg status (the same FFmpeg
   it will use at runtime).
2. A short public video downloads, the file appears in Downloads, and the
   History row appears.
3. The log file exists in the app data folder (`~/Library/Application
   Support/YTDownloader/logs/app.log` on macOS). Any missing-module error from
   PyInstaller shows up there first.

A quick non-interactive check that the bundle's Python and Tk came along is to
launch the bundled binary and immediately quit — if Tk/Tcl were missing, the app
would exit with a traceback instead of showing a window.

## 4. Tcl/Tk

This is the one thing that reliably bites with standalone CPython builds
(`uv`/python-build-standalone, some Homebrew builds). The details matter, so
here is the whole story:

1. Those interpreters keep the Tcl/Tk *script* libraries in
   `<base_prefix>/lib/tcl8.6` and `.../lib/tk8.6`, but Tcl's compiled-in lookup
   misses them, so `tkinter.Tcl()` only initialises when `TCL_LIBRARY` (and
   usually `TK_LIBRARY`) point at those folders. That is why
   `main.py::configure_tcl_tk()` sets them at runtime.
2. At **build** time, PyInstaller's `pre_find_module_path/hook-tkinter.py`
   probes Tcl/Tk and, if the probe fails, logs
   `tkinter installation is broken. It will be excluded from the application`
   and removes tkinter from the bundle. The build then *succeeds* and produces
   an app that dies immediately with
   `Could not load the UI … No module named 'tkinter'`.

The spec therefore calls `_point_tcl_tk()` (the same logic as `main.py`) before
`Analysis(...)`, which makes the probe succeed and lets PyInstaller collect
`tkinter`, `_tkinter` and the Tcl/Tk script libraries (as `_tcl_data/` and
`_tk_data/`). Verify with:

```bash
# should print "True" — reproduce the hook's own probe:
.venv/bin/python -c "from PyInstaller.utils.hooks import tcl_tk; print(tcl_tk.tcltk_info.available)"

# should list tcl/tk data folders (proves the build collected them):
ls "dist/YT Downloader.app/Contents/Resources" | grep -E "_tcl_data|_tk_data"
```

Further cases:

- **The Tcl/Tk folders are somewhere unusual.** Point the environment at them
  before building; the spec only fills in the variables if they're unset:

  ```bash
  TCL_LIBRARY=/path/to/tcl8.6 TK_LIBRARY=/path/to/tk8.6 \
    .venv/bin/python -m PyInstaller packaging/yt_downloader.spec --noconfirm --clean
  ```

- **`ModuleNotFoundError: No module named '_tkinter'` when testing the probe.**
  The interpreter used to build the venv has no Tk at all. Use one that does
  (a `uv`-managed CPython or python.org's installer) and rebuild — this is
  exactly why the development environment here uses a `uv` CPython 3.14 rather
  than a Tk-less system Python.
- **App starts and exits, log mentions `init.tcl` / `tk.tcl`.** The bundle has
  `_tkinter` but not the script libraries; check the `ls` command above and
  confirm the build used this spec (the `_point_tcl_tk()` call is what makes
  PyInstaller locate them).

## 5. Bundle contents and size

The spec collects:

- the app (`main.py`, `app/`, `ui/`, `downloader/`, `services/`, `database/`);
- `assets/` (the generated icons);
- **every** `yt_dlp` submodule — that's what keeps "all sites yt-dlp supports"
  working. It is also the largest part of the bundle (tens of MB). If you want a
  smaller bundle for a fixed set of sites, replace
  `collect_submodules("yt_dlp")` with explicit hidden imports such as
  `yt_dlp.extractor.youtube` — at the cost of breaking other sites;
- `customtkinter`'s theme JSON and fonts (`.ttf`) as data files;
- Pillow's Tk helper (`PIL._tkinter_finder`) and `darkdetect`/`packaging`.

FFmpeg is **not** bundled (it's a large third-party binary with its own
licence). Each user installs it once; the app detects it on `PATH` or from
**Settings → FFmpeg path**. Mention `brew install ffmpeg` in your release notes.

## 6. Distribution

### macOS

```bash
# ad-hoc signature, good enough for your own machine:
codesign --force --deep --sign - "dist/YT Downloader.app"

# distributable build (requires an Apple Developer ID):
codesign --force --deep --options runtime \
  --sign "Developer ID Application: Your Name (TEAMID)" "dist/YT Downloader.app"
xcrun notarytool submit "dist/YT Downloader.app.zip" \
  --apple-id you@example.com --team-id TEAMID --password "@keychain:AC_PASSWORD" --wait
xcrun stapler staple "dist/YT Downloader.app"
```

Unsigned bundles require the user to right-click → **Open** the first time
(Gatekeeper quarantine). Sign/notarise if you're handing this to anyone else.

### Windows / Linux

Zip the `dist/YT Downloader/` folder and ship it. The executable inside is
self-contained. Linux users may still need system Tk (`libtk8.6`) on very old
distributions — PyInstaller bundles the `_tkinter` module it was built with, so
this is rare.

## 7. Rebuilding after changes

```bash
.venv/bin/python -m PyInstaller packaging/yt_downloader.spec --noconfirm --clean
```

`--clean` clears PyInstaller's cache; drop it for faster incremental builds. If
you add a new dependency or a new asset folder, update `datas`/`hiddenimports`
in the spec.

## 8. Dependency-free alternative

For a developer-facing release you can skip PyInstaller entirely:

```bash
git clone <repo> && cd YTDownload
uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python main.py
```

Document the FFmpeg install step either way — merging/conversion needs it.
