"""On-disk cache of raw API responses so re-runs work offline.

Distinct from test fixtures: this is a runtime feature. The first fetch writes
the raw JSON under ``snapshots/<key>.json``; later runs replay it. ``refresh``
forces a re-fetch and overwrites. Source hygiene per DESIGN: every raw pull is
snapshotted so rebuilds don't re-hit the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


class SnapshotCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def has(self, key: str) -> bool:
        return self._path(key).exists()

    def get_json(
        self,
        key: str,
        fetch: Callable[[], Any],
        *,
        refresh: bool = False,
    ) -> Any:
        """Return cached JSON for ``key``, or call ``fetch`` and cache it.

        On a hit (and not ``refresh``) ``fetch`` is never called.
        """
        path = self._path(key)
        if path.exists() and not refresh:
            return json.loads(path.read_text())

        data = fetch()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=0, ensure_ascii=False))
        return data
