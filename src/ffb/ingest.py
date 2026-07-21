"""Wire the ingest half of the spine: snapshot -> parse -> resolve -> store.

Two entry points:

- ``ensure_crosswalk`` loads the nflverse ff_playerids identity spine.
- ``ensure_ingested`` loads a source's projections, resolving each native id to
  the canonical ``player_key`` via the crosswalk. Crosswalk misses are never
  dropped: they fall back to a ``source:native_id`` key and are counted in the
  returned :class:`Reconciliation` so the CLI and logs can surface them.

Both are idempotent and offline-capable: raw responses come from the snapshot
cache when present.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ffb import config, names
from ffb.snapshot import SnapshotCache
from ffb.sources import crosswalk, espn, ffc, sleeper
from ffb.store import Store

log = logging.getLogger(__name__)


@dataclass
class Reconciliation:
    """Outcome of resolving one source's rows against the crosswalk."""

    source: str
    n_rows: int = 0
    matched: int = 0
    unmatched: int = 0
    unmatched_names: list[str] = field(default_factory=list)


def ensure_crosswalk(
    store: Store,
    cache: SnapshotCache,
    *,
    refresh: bool = False,
    fetch: Callable[[], list[dict[str, Any]]] | None = None,
) -> int:
    """Ensure the crosswalk spine is loaded. Returns rows ingested (0 if already
    present and not refreshing)."""
    if store.has_crosswalk() and not refresh:
        log.debug("crosswalk already loaded; skipping")
        return 0

    fetch_fn = fetch or crosswalk.fetch_playerids
    # Gate the snapshot write on a parseable pull so a transient empty/malformed
    # refresh can't overwrite the known-good cache (which an offline fresh-DB
    # rebuild would then replay as empty).
    raw = cache.get_json(
        crosswalk.snapshot_key(),
        fetch_fn,
        refresh=refresh,
        is_valid=lambda data: bool(crosswalk.parse_crosswalk(data)),
    )
    rows = crosswalk.parse_crosswalk(raw)
    if not rows:
        # parse_crosswalk drops malformed records without raising, so a transient
        # upstream problem can yield zero rows. Never let that empty pull wipe a
        # usable spine (replace_crosswalk deletes first); keep what we have.
        log.warning("crosswalk pull parsed to 0 rows; keeping existing spine")
        return 0
    # Mirror the source (replace, don't union) so a refresh drops mappings that
    # disappeared or were reassigned upstream, rather than leaving stale rows
    # that resolve() could still match.
    store.replace_crosswalk(rows)
    log.info("loaded %d crosswalk rows", len(rows))
    return len(rows)


def resolve_rows(
    store: Store, rows: list[dict[str, Any]], source: str
) -> tuple[list[dict[str, Any]], Reconciliation]:
    """Attach a canonical ``player_key`` + ``matched`` flag to each parsed row.

    A crosswalk miss keeps the player under a ``source:native_id`` fallback key
    (``matched=False``) rather than dropping it.
    """
    lookup = store.resolve_batch(source, [row["native_id"] for row in rows])
    recon = Reconciliation(source=source, n_rows=len(rows))
    resolved: list[dict[str, Any]] = []
    for row in rows:
        hit = lookup.get(row["native_id"])
        if hit is not None:
            recon.matched += 1
            # Matched players take canonical crosswalk identity (consistent
            # across sources), so ESPN can't clobber Sleeper's team, etc.
            resolved.append(
                {
                    **row,
                    "player_key": hit["player_key"],
                    "matched": True,
                    "full_name": hit["full_name"] or row["full_name"],
                    "position": hit["position"] or row["position"],
                    "team": hit["team"] or row["team"],
                }
            )
        else:
            recon.unmatched += 1
            if len(recon.unmatched_names) < 20:
                recon.unmatched_names.append(row.get("full_name") or row["native_id"])
            resolved.append(
                {
                    **row,
                    "player_key": f"{source}:{row['native_id']}",
                    "matched": False,
                }
            )
    return resolved, recon


def _finalize(store: Store, rows: list[dict[str, Any]], season: int, source: str) -> Reconciliation:
    """Resolve parsed rows, replace the source's slice, and report the outcome."""
    resolved, recon = resolve_rows(store, rows, source)
    # Mirror the source: drop the existing slice so a refresh can't leave behind
    # players no longer in the fresh snapshot.
    store.delete_projections(season, source=source)
    store.upsert_projections(resolved)
    log.info(
        "ingested %d %s rows for %s (%d matched, %d unmatched)",
        recon.n_rows,
        source,
        season,
        recon.matched,
        recon.unmatched,
    )
    if recon.unmatched:
        log.warning(
            "%d %s players unmatched to crosswalk: %s",
            recon.unmatched,
            source,
            ", ".join(recon.unmatched_names),
        )
    return recon


def _can_skip(store: Store, season: int, source: str, refresh: bool) -> bool:
    """Skip re-ingest only when the source's season slice is present, not being
    refreshed, and has no stale (now-resolvable) rows to re-resolve.

    The presence check is scoped to ``season`` to match what these entry points
    ingest: a weekly-scope row (slice 9) must not make the season slice look
    present and get skipped.
    """
    return (
        store.has_season(season, source=source, scope="season")
        and not refresh
        and not store.has_stale_resolution(season, source)
    )


def ensure_ingested(
    store: Store,
    cache: SnapshotCache,
    season: int,
    *,
    refresh: bool = False,
    fetch: Callable[[], list[dict[str, Any]]] | None = None,
) -> Reconciliation:
    """Ensure ``season`` Sleeper projections are stored, resolved to player_key.

    Idempotent: skips when already present, unless ``refresh`` (network re-fetch)
    or a crosswalk that arrived after ingest now makes stranded rows resolvable
    (re-resolved offline from the cached snapshot). Returns a
    :class:`Reconciliation` (all-zero when skipped).
    """
    if _can_skip(store, season, "sleeper", refresh):
        log.debug("sleeper season %s already ingested; skipping", season)
        return Reconciliation(source="sleeper")

    fetch_fn = fetch or (lambda: sleeper.fetch_projections(season))
    raw = cache.get_json(sleeper.snapshot_key(season), fetch_fn, refresh=refresh)
    return _finalize(store, sleeper.parse_projections(raw), season, "sleeper")


def ensure_espn_ingested(
    store: Store,
    cache: SnapshotCache,
    season: int,
    *,
    refresh: bool = False,
    fetch: Callable[[], list[dict[str, Any]]] | None = None,
) -> Reconciliation:
    """Ensure ``season`` ESPN projections are stored, resolved to player_key.

    Same idempotency + late-crosswalk self-healing as :func:`ensure_ingested`.
    """
    if _can_skip(store, season, "espn", refresh):
        log.debug("espn season %s already ingested; skipping", season)
        return Reconciliation(source="espn")

    fetch_fn = fetch or (lambda: espn.fetch_projections(season))
    raw = cache.get_json(espn.snapshot_key(season), fetch_fn, refresh=refresh)
    return _finalize(store, espn.parse_projections(raw, season), season, "espn")


def resolve_adp_rows(
    store: Store, rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], Reconciliation]:
    """Attach a ``player_key`` + ``matched`` flag to parsed ADP rows by name.

    FFC has no id in the crosswalk, so we resolve by ``(name, position)`` with a
    team tiebreak (see :mod:`ffb.names`). A miss is never dropped: it keeps an
    ``ffc:<native_id>`` fallback key (``matched=False``) and is counted in the
    returned :class:`Reconciliation`.
    """
    index = names.build_name_index(store.crosswalk_rows())
    recon = Reconciliation(source="ffc", n_rows=len(rows))
    resolved: list[dict[str, Any]] = []
    for row in rows:
        key = names.match_by_name(index, row["full_name"], row["position"], team=row.get("team"))
        if key is not None:
            recon.matched += 1
            resolved.append({**row, "player_key": key, "matched": True})
        else:
            recon.unmatched += 1
            if len(recon.unmatched_names) < 20:
                recon.unmatched_names.append(row.get("full_name") or row["native_id"])
            resolved.append({**row, "player_key": f"ffc:{row['native_id']}", "matched": False})
    return resolved, recon


def ensure_adp_ingested(
    store: Store,
    cache: SnapshotCache,
    season: int,
    *,
    refresh: bool = False,
    teams: int = config.LEAGUE_NUM_TEAMS,
    fmt: str = config.FFC_FORMAT,
    fetch: Callable[[], dict[str, Any]] | None = None,
) -> Reconciliation:
    """Ensure ``season`` FFC ADP is stored, name-resolved to ``player_key``.

    Unlike the id-resolved sources, ADP has no staleness detector (its resolution
    is name-based, invisible to ``has_stale_resolution``). It instead re-parses +
    re-resolves from the cached snapshot on **every** run (delete-then-insert),
    which keeps the mirror honest and buys the late-crosswalk self-heal for free
    (§3c). Network is still only touched on ``refresh`` or the first pull.
    """
    fetch_fn = fetch or (lambda: ffc.fetch_adp(season, teams=teams, fmt=fmt))
    # Gate the snapshot write on a successful parse so a transient bad refresh
    # can't overwrite the known-good cache (mirrors ensure_crosswalk).
    raw = cache.get_json(
        ffc.snapshot_key(season, teams=teams, fmt=fmt),
        fetch_fn,
        refresh=refresh,
        is_valid=lambda data: bool(ffc.parse_adp(data, season)),
    )
    rows = ffc.parse_adp(raw, season)
    if not rows:
        # An invalid/empty pull (e.g. status != Success) must be surfaced as a
        # failure, not swallowed: otherwise the CLI would silently serve the
        # previously persisted slice as current market data. The is_valid gate
        # above already preserved the good on-disk snapshot, so an offline rerun
        # re-resolves from it.
        raise ValueError(f"FFC ADP pull for {season} returned no usable rows")

    resolved, recon = resolve_adp_rows(store, rows)
    store.delete_adp(season)
    store.upsert_adp(resolved)
    log.info(
        "ingested %d FFC ADP rows for %s (%d matched, %d unmatched)",
        recon.n_rows,
        season,
        recon.matched,
        recon.unmatched,
    )
    if recon.unmatched:
        log.warning(
            "%d FFC ADP players unmatched to crosswalk: %s",
            recon.unmatched,
            ", ".join(recon.unmatched_names),
        )
    return recon
