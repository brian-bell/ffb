"""nflverse season schedule source: complete team bye weeks.

Pulled via ``nflreadpy.load_schedules`` (spike-verified 2026-07-23: 272 REG
games, columns ``season/game_type/week/home_team/away_team``, team codes in
nflverse style — ``KC``/``SF``/``LA`` — aliased to canonical MFL-style codes
via :func:`ffb.identity.canonical_team`).

A team's bye is the single missing week in ``1..max_week`` across its
regular-season games. Zero or multiple missing weeks (incomplete data) means
the team is skipped — never guessed — so byes are exhaustive only when the
published schedule is. ``parse_byes`` is pure and never raises on a bad row.
"""

from __future__ import annotations

import logging
from typing import Any

from ffb import config, identity

log = logging.getLogger(__name__)

SOURCE = "schedule"


def missing_teams(rows: list[dict[str, Any]]) -> list[str]:
    """Canonical teams absent from parsed bye rows (empty when complete).

    The full NFL team set is known, so a schedule pull is valid only when every
    team derived a bye. A partial-but-parseable pull (truncated response, an
    unresolved code) must not pass the snapshot gate or replace a complete
    mirror with a subset.
    """
    return sorted(config.NFL_TEAM_CODES - {row["team"] for row in rows})


def snapshot_key(season: int) -> str:
    return f"nflverse/schedule_{season}"


# Columns pulled from the wide schedules frame into the snapshot.
_FETCH_COLS = ("season", "game_type", "week", "home_team", "away_team")


def fetch_schedule(season: int) -> list[dict[str, Any]]:
    """Fetch the season schedule from nflverse as record dicts. Hits the network.

    The polars frame never escapes this module (same pattern as the crosswalk
    source): select the columns we use and return plain ``list[dict]``.
    """
    import nflreadpy as nfl

    df = nfl.load_schedules(seasons=[season])
    cols = [c for c in _FETCH_COLS if c in df.columns]
    return df.select(cols).to_dicts()


def parse_byes(raw: Any, season: int) -> list[dict[str, Any]]:
    """Derive one bye-week row per canonical team from raw schedule records.

    Returns ``[]`` for an empty or wrong-shaped pull (the snapshot ``is_valid``
    gate relies on that). Unknown team codes and teams with an ambiguous bye
    (not exactly one missing week) are logged and skipped, never guessed.
    """
    if not isinstance(raw, list):
        log.warning("schedule pull is not a list; no bye rows")
        return []

    weeks_by_team: dict[str, set[int]] = {}
    max_week = 0
    for game in raw:
        if not isinstance(game, dict):
            log.warning("skip non-object schedule entry: %r", game)
            continue
        if game.get("game_type") != "REG":
            continue
        week = game.get("week")
        if not isinstance(week, int):
            log.warning("skip schedule game with missing/invalid week: %s", game)
            continue
        max_week = max(max_week, week)
        for side in ("home_team", "away_team"):
            team = identity.canonical_team(game.get(side))
            if team is None:
                log.warning("skip unknown schedule team code %r (week %s)", game.get(side), week)
                continue
            weeks_by_team.setdefault(team, set()).add(week)

    rows: list[dict[str, Any]] = []
    for team in sorted(weeks_by_team):
        missing = set(range(1, max_week + 1)) - weeks_by_team[team]
        if len(missing) != 1:
            log.warning("skip team %s with ambiguous bye (missing weeks %s)", team, sorted(missing))
            continue
        rows.append({"season": season, "source": SOURCE, "team": team, "bye": missing.pop()})
    return rows
