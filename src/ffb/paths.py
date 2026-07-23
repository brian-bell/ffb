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


def export_dir() -> Path:
    """Default directory for ``ffb board export`` output.

    Defaults to ``<repo>/exports/`` (gitignored); ``FFB_EXPORT_DIR`` overrides so
    tests can point at a temp dir and a user can redirect the board contract.
    """
    return Path(os.environ.get("FFB_EXPORT_DIR", config.REPO_ROOT / "exports"))
