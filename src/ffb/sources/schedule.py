"""nflverse season schedule source: team bye weeks and regular-season games.

Pulled via ``nflreadpy.load_schedules`` (spike-verified 2026-07-23: 272 REG
games, columns ``season/game_type/week/home_team/away_team``, plus
``gameday``/``gametime`` when the frame has them). Team codes are nflverse
style — ``KC``/``SF``/``LA`` — aliased to canonical MFL-style codes via
:func:`ffb.identity.canonical_team`).

A team's bye is the single missing week in ``1..max_week`` across its
regular-season games. Zero or multiple missing weeks (incomplete data) means
the team is skipped — never guessed — so byes are exhaustive only when the
published schedule is. ``parse_byes`` is pure and never raises on a bad row.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from ffb import config, identity

_EASTERN = ZoneInfo("America/New_York")

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
# ``gameday``/``gametime`` are optional: older snapshots omit them, and
# ``parse_games`` then leaves ``kickoff_at`` null.
_FETCH_COLS = ("season", "game_type", "week", "home_team", "away_team", "gameday", "gametime")


def fetch_schedule(season: int) -> list[dict[str, Any]]:
    """Fetch the season schedule from nflverse as record dicts. Hits the network.

    The polars frame never escapes this module (same pattern as the crosswalk
    source): select the columns we use and return plain ``list[dict]``.
    """
    import nflreadpy as nfl

    log.info("api request provider=nflverse operation=load_schedules seasons=%s", season)
    df = nfl.load_schedules(seasons=[season])
    cols = [c for c in _FETCH_COLS if c in df.columns]
    rows = [_json_schedule_row(row) for row in df.select(cols).to_dicts()]
    log.info("api response provider=nflverse operation=load_schedules items=%s", len(rows))
    return rows


def _json_schedule_row(row: dict[str, Any]) -> dict[str, Any]:
    """Snapshot row. Dates and clock times are JSON strings, never date objects."""
    if not isinstance(row, dict):
        return row
    out = dict(row)
    if "gameday" in out:
        out["gameday"] = _day_text(out["gameday"])
    if "gametime" in out:
        out["gametime"] = _clock_text(out["gametime"])
    return out


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
        if game.get("season") != season:
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


def parse_games(raw: Any, season: int) -> list[dict[str, Any]]:
    """Return canonical regular-season games from a raw schedule pull.

    Unknown team codes and malformed rows are skipped, never guessed. Returns
    ``[]`` for an empty or wrong-shaped payload so callers can treat games as
    additive to the bye completeness gate.
    """
    if not isinstance(raw, list):
        log.warning("schedule pull is not a list; no game rows")
        return []

    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, str]] = set()
    for game in raw:
        if not isinstance(game, dict):
            log.warning("skip non-object schedule entry: %r", game)
            continue
        if game.get("season") != season:
            continue
        if game.get("game_type") != "REG":
            continue
        week = game.get("week")
        if not isinstance(week, int):
            log.warning("skip schedule game with missing/invalid week: %s", game)
            continue
        home = identity.canonical_team(game.get("home_team"))
        away = identity.canonical_team(game.get("away_team"))
        if home is None or away is None:
            log.warning(
                "skip schedule game with unknown team home=%r away=%r (week %s)",
                game.get("home_team"),
                game.get("away_team"),
                week,
            )
            continue
        key = (season, week, home, away)
        if key in seen:
            continue
        seen.add(key)
        gameday = _day_text(game.get("gameday"))
        gametime = _clock_text(game.get("gametime"))
        rows.append(
            {
                "season": season,
                "source": SOURCE,
                "week": week,
                "home_team": home,
                "away_team": away,
                "gameday": gameday,
                "gametime": gametime,
                "kickoff_at": _kickoff_at(gameday, gametime),
            }
        )
    rows.sort(key=lambda row: (row["week"], row["home_team"], row["away_team"]))
    return rows


def timed_game_count(raw: Any, season: int) -> int:
    """Regular-season games in ``raw`` with a parseable kickoff."""
    return sum(1 for game in parse_games(raw, season) if game.get("kickoff_at"))


def kickoff_refresh_error(previous: Any, fresh: Any, season: int) -> str | None:
    """Why a refresh must not replace the cached schedule, or None to accept it.

    A payload with no ``gameday``/``gametime`` keys, or fewer parseable kickoffs
    than the snapshot already on disk, is rejected. Reading an older snapshot
    that omits the keys is a separate path and stays valid.
    """
    if not _has_kickoff_keys(fresh):
        return (
            f"schedule refresh for {season} is missing gameday/gametime; "
            "keeping the cached snapshot"
        )
    fresh_timed = timed_game_count(fresh, season)
    previous_timed = timed_game_count(previous, season)
    if fresh_timed < previous_timed:
        return (
            f"schedule refresh for {season} has {fresh_timed} games with kickoff times, "
            f"fewer than the cached snapshot's {previous_timed}; keeping the cached snapshot"
        )
    return None


def _has_kickoff_keys(raw: Any) -> bool:
    if not isinstance(raw, list):
        return False
    return any(isinstance(row, dict) and ("gameday" in row or "gametime" in row) for row in raw)


def team_kickoffs(games: list[dict[str, Any]]) -> dict[tuple[int, str], str]:
    """UTC kickoff for each ``(week, canonical team)`` that has a clock time.

    Accepts parsed games (``kickoff_at`` or ``gameday``/``gametime``) and rows
    read back from ``schedule_games``. A missing or unparsed time is omitted;
    scoring then drops that team-week instead of guessing.
    """
    kickoffs: dict[tuple[int, str], str] = {}
    for game in games:
        if not isinstance(game, dict):
            continue
        week = game.get("week")
        if type(week) is not int:
            continue
        stamp = game.get("kickoff_at")
        if not isinstance(stamp, str) or not stamp:
            stamp = _kickoff_at(game.get("gameday"), game.get("gametime"))
        if not stamp:
            continue
        for side in ("home_team", "away_team"):
            team = identity.canonical_team(game.get(side))
            if team is not None:
                kickoffs[(week, team)] = stamp
    return kickoffs


def _kickoff_at(gameday: object, gametime: object) -> str | None:
    """UTC kickoff from nflverse ``gameday`` + ``gametime`` (US Eastern).

    Missing, ``NA``, or unparsed times stay null. Scoring then excludes that
    team-week instead of guessing a cutoff.
    """
    day = _day_text(gameday)
    clock = _clock_text(gametime)
    if day is None or clock is None:
        return None
    try:
        local = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    aware = local.replace(tzinfo=_EASTERN)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_text(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 10 and text[:4].isdigit():
            return text[:10]
    return None


def _clock_text(value: object) -> str | None:
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.upper() in {"NA", "N/A", "TBD", "NULL", "NONE"}:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"
