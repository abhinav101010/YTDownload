"""Root conftest: makes the project importable and isolates test data.

``YTDL_APP_DATA_DIR`` is set *before* any application module is imported so
that nothing a test does can touch the user's real settings/history database.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "YTDL_APP_DATA_DIR", tempfile.mkdtemp(prefix="ytdl-pytest-data-")
)
