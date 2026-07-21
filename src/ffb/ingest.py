"""Wire the ingest half of the spine: snapshot -> parse -> store.

``ensure_ingested`` is idempotent: if the store already holds the season it
returns immediately (unless ``refresh``). The raw response is served from the
snapshot cache when present, so ordinary runs are offline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ffb.snapshot import SnapshotCache
from ffb.sources import sleeper
from ffb.store import Store

log = logging.getLogger(__name__)


def ensure_ingested(
    store: Store,
    cache: SnapshotCache,
    season: int,
    *,
    refresh: bool = False,
    fetch: Callable[[], list[dict[str, Any]]] | None = None,
) -> int:
    """Ensure ``season`` projections are in the store. Returns rows ingested
    (0 if the season was already present and not refreshing).
    """
    if store.has_season(season) and not refresh:
        log.debug("season %s already ingested; skipping", season)
        return 0

    fetch_fn = fetch or (lambda: sleeper.fetch_projections(season))
    raw = cache.get_json(sleeper.snapshot_key(season), fetch_fn, refresh=refresh)
    rows = sleeper.parse_projections(raw)
    # Mirror the source: drop the existing slice so a refresh can't leave
    # behind players no longer in the fresh snapshot.
    store.delete_projections(season)
    store.upsert_projections(rows)
    log.info("ingested %d projection rows for %s", len(rows), season)
    return len(rows)
