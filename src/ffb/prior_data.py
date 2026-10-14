"""Snapshot 2024–2025 projection and stat pulls, then build prior facts.

Network access stays in the source fetchers and only runs when the snapshot
policy asks for it. Facts are computed at read time from those snapshots, the
crosswalk, and each league's scoring config. Nothing is written to DuckDB,
and nothing is published.
"""

from __future__ import annotations

from typing import Any

from ffb.config import ScoringConfig
from ffb.priors import build_prior_facts
from ffb.snapshot import SnapshotCache, SnapshotPolicy
from ffb.sources import crosswalk, espn, player_stats, schedule, sleeper

PRIOR_SEASONS = (2024, 2025)
PRIOR_WEEKS = tuple(range(1, 19))


def sync_priors(
    cache: SnapshotCache,
    seasons: list[int],
    *,
    policy: SnapshotPolicy = SnapshotPolicy.MISSING_ONLY,
) -> list[str]:
    """Cache schedules, weekly projections, nflverse stats, and the crosswalk.

    A missing offline snapshot or a failed fetch is reported and does not stop
    the other seasons. The caller decides whether those notes are fatal.
    """
    notes: list[str] = []
    notes.extend(
        _sync_key(
            cache,
            crosswalk.snapshot_key(),
            crosswalk.fetch_playerids,
            policy=policy,
            valid=lambda raw: isinstance(raw, list) and bool(raw),
        )
    )
    for season in seasons:
        notes.extend(
            _sync_key(
                cache,
                schedule.snapshot_key(season),
                lambda season=season: schedule.fetch_schedule(season),
                policy=policy,
                valid=lambda raw: isinstance(raw, list) and bool(raw),
            )
        )
        notes.extend(
            _sync_key(
                cache,
                player_stats.snapshot_key(season),
                lambda season=season: player_stats.fetch_player_stats(season),
                policy=policy,
                valid=_stats_valid,
            )
        )
        for week in PRIOR_WEEKS:
            notes.extend(
                _sync_key(
                    cache,
                    sleeper.snapshot_key(season, week=week),
                    lambda season=season, week=week: sleeper.fetch_projections(season, week=week),
                    policy=policy,
                    valid=lambda raw: isinstance(raw, list) and bool(raw),
                )
            )
            notes.extend(
                _sync_key(
                    cache,
                    espn.snapshot_key(season, week=week),
                    lambda season=season, week=week: espn.fetch_projections(season, week=week),
                    policy=policy,
                    valid=lambda raw: isinstance(raw, list) and bool(raw),
                )
            )
    return notes


def load_prior_facts(
    cache: SnapshotCache,
    *,
    seasons: list[int],
    scoring_by_league: dict[str, ScoringConfig],
    weeks: tuple[int, ...] = PRIOR_WEEKS,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build unselected prior facts. The caller runs ``select_scoring_facts``."""
    notes: list[str] = []
    identities = _identities(cache, notes)
    facts: list[dict[str, Any]] = []
    for season in seasons:
        actuals = _actuals(cache, season, notes)
        if actuals is None:
            continue
        by_week: dict[int, list[dict[str, Any]]] = {}
        for row in actuals:
            by_week.setdefault(row["week"], []).append(row)
        for week in weeks:
            projections, as_of = _projections(cache, season, week, notes)
            if not projections:
                continue
            for league_key, scoring in scoring_by_league.items():
                facts.extend(
                    build_prior_facts(
                        projections,
                        by_week.get(week, []),
                        identities,
                        season=season,
                        week=week,
                        league_key=league_key,
                        scoring=scoring,
                        as_of=as_of,
                    )
                )
    return facts, notes


def seasons_with_stats(
    cache: SnapshotCache, *, before: int, include: int | None = None
) -> list[int]:
    """Seasons whose player-stats snapshot is on disk and may train ``before``."""
    found: list[int] = []
    root = cache.root / "nflverse"
    if root.is_dir():
        for path in sorted(root.glob("player_stats_*.json")):
            suffix = path.name.removeprefix("player_stats_").removesuffix(".json")
            if not suffix.isdigit():
                continue
            season = int(suffix)
            if season < before or season == include:
                found.append(season)
    return found


def _sync_key(cache, key, fetch, *, policy: SnapshotPolicy, valid) -> list[str]:
    try:
        cache.get_json(key, fetch, policy=policy, is_valid=valid)
    except FileNotFoundError as exc:
        return [str(exc)]
    except (OSError, ValueError, TypeError) as exc:
        return [f"{key}: {exc}"]
    except Exception as exc:  # noqa: BLE001 - one source must not abort the other seasons
        return [f"{key}: {exc}"]
    if not cache.has(key):
        return [f"{key} was rejected and not snapshotted"]
    return []


def _stats_valid(raw: Any) -> bool:
    return (
        isinstance(raw, dict)
        and isinstance(raw.get("players"), list)
        and bool(raw["players"])
        and isinstance(raw.get("games"), list)
    )


def _identities(cache: SnapshotCache, notes: list[str]) -> list[dict[str, Any]]:
    key = crosswalk.snapshot_key()
    if not cache.has(key):
        notes.append(f"crosswalk snapshot missing ({key}); skill-player priors were not joined")
        return []
    try:
        return crosswalk.parse_crosswalk(cache.read_json(key))
    except (OSError, ValueError, TypeError) as exc:
        notes.append(f"{key}: {exc}")
        return []


def _actuals(cache: SnapshotCache, season: int, notes: list[str]) -> list[dict[str, Any]] | None:
    key = player_stats.snapshot_key(season)
    if not cache.has(key):
        notes.append(f"player stats snapshot missing ({key}); {season} priors were not built")
        return None
    try:
        return player_stats.parse_actuals(cache.read_json(key), season)
    except (OSError, ValueError, TypeError) as exc:
        notes.append(f"{key}: {exc}")
        return None


def _projections(
    cache: SnapshotCache, season: int, week: int, notes: list[str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    projections: list[dict[str, Any]] = []
    as_of: dict[str, str] = {}
    sleeper_key = sleeper.snapshot_key(season, week=week)
    if cache.has(sleeper_key):
        try:
            raw = cache.read_json(sleeper_key)
            projections.extend(sleeper.parse_projections(raw, week=week))
            as_of = sleeper.projection_as_of(raw)
        except (OSError, ValueError, TypeError) as exc:
            notes.append(f"{sleeper_key}: {exc}")
    espn_key = espn.snapshot_key(season, week=week)
    if cache.has(espn_key):
        try:
            raw = cache.read_json(espn_key)
            projections.extend(espn.parse_projections(raw, season, week=week))
        except (OSError, ValueError, TypeError) as exc:
            notes.append(f"{espn_key}: {exc}")
    return projections, as_of
