"""Policy-driven on-disk cache of raw API responses.

Distinct from test fixtures: this is a runtime feature. Missing-only fetches an
absent snapshot, refresh always fetches, and offline raises on a miss without
calling the fetch boundary. Source hygiene per DESIGN: every valid raw pull is
snapshotted so rebuilds don't re-hit the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any


class SnapshotPolicy(StrEnum):
    MISSING_ONLY = "missing-only"
    REFRESH = "refresh"
    OFFLINE = "offline"


@dataclass(frozen=True)
class SnapshotMetadata:
    key: str
    modified_at: str
    sha256: str


class SnapshotCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def has(self, key: str) -> bool:
        return self._path(key).exists()

    def metadata(self, key: str) -> SnapshotMetadata | None:
        path = self._path(key)
        if not path.exists():
            return None
        content = path.read_bytes()
        modified = (
            datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z")
        )
        return SnapshotMetadata(key=key, modified_at=modified, sha256=sha256(content).hexdigest())

    def get_json(
        self,
        key: str,
        fetch: Callable[[], Any],
        *,
        refresh: bool = False,
        policy: SnapshotPolicy | str | None = None,
        is_valid: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Return cached JSON for ``key``, or call ``fetch`` according to policy.

        On a hit (unless refreshing) ``fetch`` is never called. ``is_valid``,
        when given, gates persistence of a freshly fetched result: if it returns
        false the data is returned to the caller but the existing snapshot is
        left untouched, so a transient bad refresh can't overwrite a
        known-good cache. Replayed (cached) data is never re-validated.
        """
        selected = (
            SnapshotPolicy(policy)
            if policy is not None
            else (SnapshotPolicy.REFRESH if refresh else SnapshotPolicy.MISSING_ONLY)
        )
        path = self._path(key)
        if path.exists() and selected is not SnapshotPolicy.REFRESH:
            return json.loads(path.read_text())
        if selected is SnapshotPolicy.OFFLINE:
            raise FileNotFoundError(
                f"offline snapshot missing for {key!r}; run `ffb season sync` online first"
            )

        data = fetch()
        if is_valid is None or is_valid(data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=0, ensure_ascii=False))
        return data
