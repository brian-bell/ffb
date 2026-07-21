"""Resolve store and snapshot locations, honoring env overrides.

Keeps ``config`` free of environment logic and lets tests point the CLI at
temp dirs via ``FFB_DB_PATH`` / ``FFB_SNAPSHOT_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path

from ffb import config


def db_path() -> Path:
    return Path(os.environ.get("FFB_DB_PATH", config.DB_PATH))


def snapshot_dir() -> Path:
    return Path(os.environ.get("FFB_SNAPSHOT_DIR", config.SNAPSHOT_DIR))
