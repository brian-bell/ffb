"""Policy-driven on-disk cache of raw API responses.

Distinct from test fixtures: this is a runtime feature. Missing-only fetches an
absent snapshot, refresh always fetches, and offline raises on a miss without
calling the fetch boundary. Source hygiene per DESIGN: every valid raw pull is
snapshotted so rebuilds don't re-hit the network.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class SnapshotPolicy(StrEnum):
    MISSING_ONLY = "missing-only"
    REFRESH = "refresh"
    OFFLINE = "offline"


class SnapshotAttemptThrottled(RuntimeError):
    """A global source fetch was attempted within its minimum interval."""


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

    def _attempt_key(self, key: str) -> str:
        return f"{key}.attempt"

    @contextmanager
    def _attempt_claim_lock(self, key: str) -> Iterator[None]:
        """Serialize attempt-marker claims across local processes."""
        marker_path = self._path(self._attempt_key(key))
        lock_path = marker_path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o644,
        )
        with os.fdopen(fd, "r+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def has(self, key: str) -> bool:
        return self._path(key).exists()

    def read_json(self, key: str) -> Any:
        """Read an existing snapshot, preserving JSON parse errors for callers."""
        return json.loads(self._path(key).read_text())

    def last_attempt_at(self, key: str) -> datetime | None:
        """Read the durable provider-attempt time for a snapshot key."""
        marker_key = self._attempt_key(key)
        if not self.has(marker_key):
            return None
        try:
            value = self.read_json(marker_key).get("last_attempt_at")
            if not isinstance(value, str):
                raise ValueError("missing last_attempt_at")
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except (AttributeError, json.JSONDecodeError, ValueError):
            metadata = self.metadata(marker_key)
            if metadata is None:
                return None
            return datetime.fromisoformat(metadata.modified_at.replace("Z", "+00:00"))

    def metadata(self, key: str) -> SnapshotMetadata | None:
        path = self._path(key)
        if not path.exists():
            return None
        content = path.read_bytes()
        modified = (
            datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z")
        )
        return SnapshotMetadata(key=key, modified_at=modified, sha256=sha256(content).hexdigest())

    def put_json(self, key: str, data: Any, *, mode: int | None = None) -> None:
        """Persist an externally validated payload for ``key`` without fetching.

        For callers that stage multi-resource pulls and commit only after the
        whole set validates, so a failed refresh cannot leave a partially
        updated snapshot family. ``mode`` optionally restricts file
        permissions (e.g. ``0o600`` for responses fetched under the user's
        OAuth grant).
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=0, ensure_ascii=False)
        # Write-then-rename so an interrupted write can never truncate the
        # known-good snapshot it replaces. A private payload is owner-only
        # from creation (os.open applies mode before any byte lands), and a
        # failed write cleans up its temp file rather than leaving one behind.
        tmp_path = path.with_name(path.name + ".tmp")
        # Exclusive create (after clearing any stale temp from a crashed run)
        # so a pre-placed file or symlink cannot capture or redirect the write.
        tmp_path.unlink(missing_ok=True)
        fd = os.open(
            tmp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o644 if mode is None else mode,
        )
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
            if mode is not None:
                os.chmod(tmp_path, mode)  # os.open's mode is umask-filtered
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        log.info("snapshot saved key=%s path=%s", key, path)

    def get_json(
        self,
        key: str,
        fetch: Callable[[], Any],
        *,
        refresh: bool = False,
        policy: SnapshotPolicy | str | None = None,
        is_valid: Callable[[Any], bool] | None = None,
        attempt_interval: timedelta | None = None,
        now: datetime | None = None,
    ) -> Any:
        """Return cached JSON for ``key``, or call ``fetch`` according to policy.

        On a hit (unless refreshing) ``fetch`` is never called. ``is_valid``,
        when given, gates persistence of a freshly fetched result: if it returns
        false the data is returned to the caller but the existing snapshot is
        left untouched, so a transient bad refresh can't overwrite a
        known-good cache. Replayed (cached) data is never re-validated.

        ``attempt_interval`` adds a durable, process-safe per-key provider-call
        throttle. The cache claims ``now`` atomically immediately before
        calling ``fetch``, so exceptions and rejected results still consume
        the interval without changing accepted snapshot metadata.
        """
        selected = (
            SnapshotPolicy(policy)
            if policy is not None
            else (SnapshotPolicy.REFRESH if refresh else SnapshotPolicy.MISSING_ONLY)
        )
        path = self._path(key)
        if path.exists() and selected is not SnapshotPolicy.REFRESH:
            log.info(
                "snapshot cache hit key=%s policy=%s; replaying cached response",
                key,
                selected,
            )
            return self.read_json(key)
        if selected is SnapshotPolicy.OFFLINE:
            log.info("snapshot unavailable key=%s policy=%s; network prohibited", key, selected)
            raise FileNotFoundError(
                f"offline snapshot missing for {key!r}; run `ffb season sync` online first"
            )

        reason = "refresh" if selected is SnapshotPolicy.REFRESH else "cache-miss"
        log.info("snapshot fetch key=%s reason=%s", key, reason)
        if attempt_interval is not None:
            attempted_at = (now or datetime.now(UTC)).astimezone(UTC)
            with self._attempt_claim_lock(key):
                previous_attempt = self.last_attempt_at(key)
                if previous_attempt is not None and previous_attempt > attempted_at:
                    future_delta = previous_attempt - attempted_at
                    if future_delta <= attempt_interval:
                        raise SnapshotAttemptThrottled(
                            f"snapshot fetch throttled for {key!r}; last provider attempt "
                            "is later than the current clock and remains within the minimum "
                            f"interval of {attempt_interval}"
                        )
                    log.warning("far-future snapshot attempt marker repaired key=%s", key)
                elif (
                    previous_attempt is not None
                    and attempted_at - previous_attempt < attempt_interval
                ):
                    raise SnapshotAttemptThrottled(
                        f"snapshot fetch throttled for {key!r}; last provider attempt was "
                        f"{previous_attempt.isoformat().replace('+00:00', 'Z')} and minimum "
                        f"interval is {attempt_interval}"
                    )
                self.put_json(
                    self._attempt_key(key),
                    {"last_attempt_at": attempted_at.isoformat().replace("+00:00", "Z")},
                )
        data = fetch()
        if is_valid is None or is_valid(data):
            self.put_json(key, data)
        elif path.exists():
            log.info("snapshot rejected key=%s; preserving cached response", key)
        else:
            log.info("snapshot rejected key=%s; no snapshot written", key)
        return data
