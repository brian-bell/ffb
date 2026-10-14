"""nflverse weekly player stats for prior-season actuals.

Fetch hits nflverse (``load_player_stats`` and ``load_schedules``) and stores a
trimmed frame. Parse is pure: offensive counting stats use the same keys as
projection rows, and each team-week becomes one ``DEF`` row. Points are not
stored. Each league's ``ScoringConfig`` scores the stat line at read time.

``def_tds`` excludes fumble-recovery touchdowns (nflverse counts those only in
``fumble_recovery_tds``). ``def_tds`` maps to ``pass_int_td``. A skill player's
``fumble_recovery_tds`` maps to ``fum_rec_td``. The same stat on any other
position maps to ``def_fum_td`` on that team-week, so it is scored once.
``special_teams_tds`` maps to ``def_ret_td`` on the team row. Two-point
conversions map to ``pass_2pt``, ``rush_2pt``, and ``rec_2pt``. Interceptions
map to ``pass_int``. Sack, rush, and receiving fumbles lost sum to ``fum_lost``.
"""

from __future__ import annotations

import logging
from typing import Any

from ffb import identity

log = logging.getLogger(__name__)

SOURCE = "nflverse"

_PLAYER_COLS = (
    "player_id",
    "player_display_name",
    "position",
    "season",
    "week",
    "season_type",
    "team",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "passing_2pt_conversions",
    "sack_fumbles_lost",
    "rushing_yards",
    "rushing_tds",
    "rushing_fumbles_lost",
    "rushing_2pt_conversions",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "receiving_fumbles_lost",
    "receiving_2pt_conversions",
    "fumble_recovery_tds",
    "special_teams_tds",
    "def_sacks",
    "def_interceptions",
    "def_safeties",
    "def_punt_blocks",
    "def_pat_blocks",
    "def_fg_blocks",
    "def_tds",
    "def_2pt_made",
    "fumble_recovery_opp",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_40_49",
    "fg_made_50_59",
    "fg_made_60_",
    "pat_made",
)
_GAME_COLS = (
    "season",
    "game_type",
    "week",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
)

_OFFENSE = {
    "passing_yards": "pass_yd",
    "passing_tds": "pass_td",
    "passing_interceptions": "pass_int",
    "passing_2pt_conversions": "pass_2pt",
    "rushing_yards": "rush_yd",
    "rushing_tds": "rush_td",
    "rushing_2pt_conversions": "rush_2pt",
    "receptions": "rec",
    "receiving_yards": "rec_yd",
    "receiving_tds": "rec_td",
    "receiving_2pt_conversions": "rec_2pt",
    "fg_made_0_19": "fgm_0_19",
    "fg_made_20_29": "fgm_20_29",
    "fg_made_30_39": "fgm_30_39",
    "fg_made_40_49": "fgm_40_49",
    "pat_made": "xpm",
}
_SKILL = {"QB", "RB", "WR", "TE", "K"}
_POSITION = {"PK": "K"}
_FUMBLES_LOST = ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost")


def snapshot_key(season: int) -> str:
    return f"nflverse/player_stats_{season}"


def fetch_player_stats(season: int) -> dict[str, Any]:
    """Fetch one season of weekly stats and game scores. Hits the network."""
    import nflreadpy as nfl

    log.info("api request provider=nflverse operation=load_player_stats seasons=%s", season)
    players = nfl.load_player_stats(seasons=[season])
    player_cols = [column for column in _PLAYER_COLS if column in players.columns]
    player_rows = [_plain_row(row) for row in players.select(player_cols).to_dicts()]
    log.info(
        "api response provider=nflverse operation=load_player_stats items=%s", len(player_rows)
    )
    log.info("api request provider=nflverse operation=load_schedules seasons=%s", season)
    games = nfl.load_schedules(seasons=[season])
    game_cols = [column for column in _GAME_COLS if column in games.columns]
    game_rows = [_plain_row(row) for row in games.select(game_cols).to_dicts()]
    log.info("api response provider=nflverse operation=load_schedules items=%s", len(game_rows))
    return {"players": player_rows, "games": game_rows}


def parse_actuals(raw: Any, season: int) -> list[dict[str, Any]]:
    """Normalize a player-stats snapshot into skill rows plus one DEF per team-week.

    Returns ``[]`` for a wrong-shaped payload. A player with no decoded stats
    is skipped. A team-week with no numeric score omits the points-allowed
    band instead of treating the game as a shutout.
    """
    if not isinstance(raw, dict):
        log.warning("player stats pull is not an object; no actuals")
        return []
    players = raw.get("players")
    games = raw.get("games")
    if not isinstance(players, list):
        log.warning("player stats pull has no players list; no actuals")
        return []
    rows: list[dict[str, Any]] = []
    defense: dict[tuple[int, str], dict[str, float]] = {}
    for item in players:
        parsed = _player_row(item, season)
        if parsed is None:
            continue
        skill, team_defense = parsed
        if skill is not None:
            rows.append(skill)
        if team_defense is None:
            continue
        week, team, stats = team_defense
        bucket = defense.setdefault((week, team), {})
        for key, value in stats.items():
            bucket[key] = bucket.get(key, 0.0) + value
    allowed = _points_allowed(games if isinstance(games, list) else [], season)
    for week, team in allowed:
        defense.setdefault((week, team), {})
    for (week, team), stats in sorted(defense.items()):
        band = allowed.get((week, team))
        if band is not None:
            stats = {**stats, band: 1.0}
        rows.append(
            {
                "gsis_id": None,
                "native_id": f"def:{team}",
                "name": team,
                "position": "DEF",
                "team": team,
                "week": week,
                "stats": stats,
            }
        )
    return rows


def points_allowed_band(points: int) -> str:
    """Yahoo/Sleeper points-allowed ladder key for a numeric score."""
    if points <= 0:
        return "pts_allow_0"
    if points <= 6:
        return "pts_allow_1_6"
    if points <= 13:
        return "pts_allow_7_13"
    if points <= 20:
        return "pts_allow_14_20"
    if points <= 27:
        return "pts_allow_21_27"
    if points <= 34:
        return "pts_allow_28_34"
    return "pts_allow_35p"


def _player_row(
    item: Any, season: int
) -> tuple[dict[str, Any] | None, tuple[int, str, dict[str, float]] | None] | None:
    if not isinstance(item, dict) or item.get("season") != season:
        return None
    if item.get("season_type") != "REG":
        return None
    week = item.get("week")
    if type(week) is not int or week < 1:
        return None
    team = identity.canonical_team(item.get("team"))
    if team is None:
        return None
    position = _POSITION.get(item.get("position"), item.get("position"))
    skill = None
    if position in _SKILL:
        stats = _offense_stats(item)
        long_fg = _number(item.get("fg_made_50_59")) + _number(item.get("fg_made_60_"))
        if long_fg:
            stats["fgm_50p"] = long_fg
        if stats:
            gsis_id = item.get("player_id")
            if isinstance(gsis_id, str) and gsis_id:
                skill = {
                    "gsis_id": gsis_id,
                    "native_id": None,
                    "name": item.get("player_display_name") or "",
                    "position": position,
                    "team": team,
                    "week": week,
                    "stats": stats,
                }
    defense_stats = _defense_stats(item)
    if position not in _SKILL:
        recovery_td = _optional_number(item.get("fumble_recovery_tds"))
        if recovery_td:
            defense_stats["def_fum_td"] = defense_stats.get("def_fum_td", 0.0) + recovery_td
    team_defense = (week, team, defense_stats) if defense_stats else None
    if skill is None and team_defense is None:
        return None
    return skill, team_defense


def _offense_stats(item: dict[str, Any]) -> dict[str, float]:
    stats: dict[str, float] = {}
    for source, key in _OFFENSE.items():
        value = _optional_number(item.get(source))
        if value:
            stats[key] = value
    fumbles_lost = sum(_number(item.get(source)) for source in _FUMBLES_LOST)
    if fumbles_lost:
        stats["fum_lost"] = fumbles_lost
    recovery_td = _optional_number(item.get("fumble_recovery_tds"))
    if recovery_td:
        stats["fum_rec_td"] = recovery_td
    return stats


def _defense_stats(item: dict[str, Any]) -> dict[str, float]:
    stats: dict[str, float] = {}
    pairs = (
        ("def_sacks", "sack"),
        ("def_interceptions", "int"),
        ("fumble_recovery_opp", "fum_rec"),
        ("def_safeties", "safe"),
        ("def_tds", "pass_int_td"),
        ("special_teams_tds", "def_ret_td"),
        ("def_2pt_made", "def_2pt"),
    )
    for source, key in pairs:
        value = _optional_number(item.get(source))
        if value:
            stats[key] = value
    blocks = (
        _number(item.get("def_fg_blocks"))
        + _number(item.get("def_punt_blocks"))
        + _number(item.get("def_pat_blocks"))
    )
    if blocks:
        stats["blk_kick"] = blocks
    return stats


def _points_allowed(games: list[Any], season: int) -> dict[tuple[int, str], str]:
    allowed: dict[tuple[int, str], str] = {}
    for game in games:
        if not isinstance(game, dict) or game.get("season") != season:
            continue
        if game.get("game_type") != "REG":
            continue
        week = game.get("week")
        if type(week) is not int:
            continue
        home = identity.canonical_team(game.get("home_team"))
        away = identity.canonical_team(game.get("away_team"))
        home_score = _optional_int(game.get("home_score"))
        away_score = _optional_int(game.get("away_score"))
        if home is not None and away_score is not None:
            allowed[(week, home)] = points_allowed_band(away_score)
        if away is not None and home_score is not None:
            allowed[(week, away)] = points_allowed_band(home_score)
    return allowed


def _plain_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _plain(value) for key, value in row.items()}


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:
            return None
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        return value
    return str(value)


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None
        return float(value)
    return None


def _number(value: object) -> float:
    parsed = _optional_number(value)
    return 0.0 if parsed is None else parsed


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)
