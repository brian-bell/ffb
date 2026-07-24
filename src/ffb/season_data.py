"""Application service for explicit season-data synchronization and inspection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ffb.ingest import (
    ensure_adp_ingested,
    ensure_crosswalk,
    ensure_espn_ingested,
    ensure_ingested,
    ensure_schedule_ingested,
)
from ffb.league_context import load_league_context
from ffb.snapshot import SnapshotCache, SnapshotPolicy
from ffb.sources import crosswalk, espn, ffc, schedule, sleeper
from ffb.store import Store

DEFAULT_SOURCES = ("sleeper", "espn", "ffc", "schedule")
ALL_SOURCES = ("crosswalk", *DEFAULT_SOURCES)
SOURCE_KIND = {
    "crosswalk": "identity",
    "sleeper": "projections",
    "espn": "projections",
    "ffc": "adp",
    "schedule": "schedule",
}


@dataclass(frozen=True)
class SyncResult:
    source: str
    state: str
    rows: int
    matched: int
    error: str | None = None


def expand_sources(selectors: list[str] | None) -> list[str]:
    selected = selectors or ["all"]
    if "all" in selected and len(selected) > 1:
        raise ValueError("'all' cannot be combined with other source selectors")
    expansion = {
        "all": DEFAULT_SOURCES,
        "projections": ("sleeper", "espn"),
        "adp": ("ffc",),
        "sleeper": ("sleeper",),
        "espn": ("espn",),
        "ffc": ("ffc",),
        "schedule": ("schedule",),
    }
    output: list[str] = []
    for selector in selected:
        try:
            sources = expansion[selector]
        except KeyError as exc:
            raise ValueError(f"unknown source selector: {selector}") from exc
        for source in sources:
            if source not in output:
                output.append(source)
    return output


class SeasonDataService:
    """Small service boundary hiding source orchestration and state persistence."""

    def __init__(
        self,
        store: Store,
        cache: SnapshotCache,
        *,
        fetchers: dict[str, Callable[[], Any]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.cache = cache
        self.fetchers = fetchers or {}
        self.clock = clock or (lambda: datetime.now(UTC))

    def sync(
        self,
        season: int,
        *,
        selectors: list[str] | None = None,
        policy: SnapshotPolicy = SnapshotPolicy.MISSING_ONLY,
        rebuild: bool = False,
    ) -> list[SyncResult]:
        sources = ["crosswalk", *expand_sources(selectors)]
        return [
            self._sync_one(season, source, policy=policy, rebuild=rebuild) for source in sources
        ]

    def _sync_one(
        self, season: int, source: str, *, policy: SnapshotPolicy, rebuild: bool
    ) -> SyncResult:
        attempted_at = self.clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
        refresh = policy is SnapshotPolicy.REFRESH
        league = load_league_context(self.store, season)
        snapshot_key = {
            "crosswalk": crosswalk.snapshot_key(),
            "sleeper": sleeper.snapshot_key(season),
            "espn": espn.snapshot_key(season),
            "ffc": ffc.snapshot_key(season, teams=league.num_teams),
            "schedule": schedule.snapshot_key(season),
        }[source]
        force_rebuild = rebuild or not self.cache.has(snapshot_key)
        try:
            if source == "crosswalk":
                ensure_crosswalk(
                    self.store,
                    self.cache,
                    refresh=refresh,
                    policy=policy,
                    rebuild=force_rebuild,
                    fetch=self.fetchers.get(source),
                )
            elif source == "sleeper":
                ensure_ingested(
                    self.store,
                    self.cache,
                    season,
                    refresh=refresh,
                    policy=policy,
                    rebuild=force_rebuild,
                    fetch=self.fetchers.get(source),
                )
            elif source == "espn":
                ensure_espn_ingested(
                    self.store,
                    self.cache,
                    season,
                    refresh=refresh,
                    policy=policy,
                    rebuild=force_rebuild,
                    fetch=self.fetchers.get(source),
                )
            elif source == "schedule":
                ensure_schedule_ingested(
                    self.store,
                    self.cache,
                    season,
                    refresh=refresh,
                    policy=policy,
                    rebuild=force_rebuild,
                    fetch=self.fetchers.get(source),
                )
            else:
                ensure_adp_ingested(
                    self.store,
                    self.cache,
                    season,
                    refresh=refresh,
                    policy=policy,
                    rebuild=force_rebuild,
                    teams=league.num_teams,
                    fetch=self.fetchers.get(source),
                )
            rows, matched = self.store.source_counts(season, source)
            metadata = self.cache.metadata(snapshot_key)
            self.store.upsert_season_source_state(
                {
                    "season": season,
                    "source": source,
                    "latest_attempt_status": "ready",
                    "last_attempt_at": attempted_at,
                    "last_success_at": attempted_at,
                    "row_count": rows,
                    "match_count": matched,
                    "snapshot_key": metadata.key if metadata else snapshot_key,
                    "snapshot_modified_at": metadata.modified_at if metadata else None,
                    "snapshot_sha256": metadata.sha256 if metadata else None,
                    "latest_error": None,
                }
            )
            return SyncResult(source, "ready", rows, matched)
        except Exception as exc:  # noqa: BLE001 - aggregate every requested source
            previous = self._tracked(source, season)
            rows, matched = self.store.source_counts(season, source)
            metadata = self.cache.metadata(snapshot_key)
            self.store.upsert_season_source_state(
                {
                    "season": season,
                    "source": source,
                    "latest_attempt_status": "failed",
                    "last_attempt_at": attempted_at,
                    "last_success_at": previous.get("last_success_at") if previous else None,
                    "row_count": rows,
                    "match_count": matched,
                    "snapshot_key": previous.get("snapshot_key")
                    if previous
                    else metadata.key
                    if metadata
                    else None,
                    "snapshot_modified_at": previous.get("snapshot_modified_at")
                    if previous
                    else metadata.modified_at
                    if metadata
                    else None,
                    "snapshot_sha256": previous.get("snapshot_sha256")
                    if previous
                    else metadata.sha256
                    if metadata
                    else None,
                    "latest_error": str(exc),
                }
            )
            return SyncResult(source, "failed", rows, matched, str(exc))

    def _tracked(self, source: str, season: int) -> dict[str, Any] | None:
        rows = self.store.season_source_state(season, source)
        return rows[0] if rows else None

    def status(self, season: int) -> dict[str, Any]:
        tracked = {row["source"]: row for row in self.store.season_source_state(season)}
        league = load_league_context(self.store, season)
        sources = []
        for source in ALL_SOURCES:
            row_count, match_count = self.store.source_counts(season, source)
            row = tracked.get(source)
            state = (
                row["latest_attempt_status"] if row else ("untracked" if row_count else "missing")
            )
            stale = (
                source in ("sleeper", "espn")
                and bool(row_count)
                and self.store.has_stale_resolution(season, source)
            ) or (
                source == "ffc"
                and bool(row_count)
                and bool(row)
                and row.get("snapshot_key") != ffc.snapshot_key(season, teams=league.num_teams)
            )
            sources.append(
                {
                    "name": source,
                    "kind": SOURCE_KIND[source],
                    "state": state,
                    "row_count": row_count,
                    "match_count": match_count,
                    "unmatched_count": row_count - match_count,
                    "stale": stale,
                    "last_attempt_at": row.get("last_attempt_at") if row else None,
                    "last_success_at": row.get("last_success_at") if row else None,
                    "snapshot": {
                        "key": row.get("snapshot_key"),
                        "modified_at": row.get("snapshot_modified_at"),
                        "sha256": row.get("snapshot_sha256"),
                    }
                    if row and row.get("snapshot_key")
                    else None,
                    "error": row.get("latest_error") if row else None,
                }
            )
        stored_league = self.store.league_context(season)
        return {
            "version": 1,
            "season": season,
            "complete": all(
                source["state"] == "ready" and not source["stale"] for source in sources
            ),
            "sources": sources,
            "league": {
                "state": "ready" if stored_league else "missing",
                "source": stored_league["source"] if stored_league else None,
                "synced_at": stored_league["synced_at"] if stored_league else None,
            },
        }

    def unmatched(self, season: int, source: str | None = None) -> list[dict[str, Any]]:
        if source is not None and source not in DEFAULT_SOURCES:
            raise ValueError(f"unknown unmatched source: {source}")
        return self.store.unmatched_rows(season, source)
