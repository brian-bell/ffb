"""Closed WeeklyActualsBundle v1 — Yahoo scoreboard and player actuals.

Sibling of ``LeagueBundle``. Extra keys fail. Live scores never belong in git;
tests use synthetic fixtures. Storage is a snapshot or Worker KV, not DuckDB.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class WeeklyActualsBundle:
    """Validated weekly scoreboard + player actuals."""

    data: dict[str, Any]

    @property
    def league(self) -> dict[str, Any]:
        return self.data["league"]

    @property
    def matchups(self) -> list[dict[str, Any]]:
        return self.data["matchups"]

    @property
    def players(self) -> list[dict[str, Any]]:
        return self.data["players"]


def parse_actuals(payload: object, *, season: int) -> WeeklyActualsBundle:
    """Validate the closed schema-v1 actuals contract before any write."""
    data = _mapping(payload, "bundle")
    _exact_keys(
        data,
        {"schema_version", "source", "synced_at", "league", "matchups", "players"},
        "bundle",
    )
    if data["schema_version"] != 1:
        raise ValueError("bundle.schema_version must be 1")
    if data["source"] not in ("fixture", "yahoo"):
        raise ValueError("bundle.source must be fixture or yahoo")
    _utc_timestamp(data["synced_at"], "bundle.synced_at")

    league = _mapping(data["league"], "league")
    _exact_keys(
        league, {"league_id", "league_key", "name", "season", "week", "num_teams"}, "league"
    )
    for key in ("league_id", "league_key", "name"):
        _string(league[key], f"league.{key}")
    _positive_int(league["season"], "league.season")
    if league["season"] != season:
        raise ValueError(
            f"requested season {season} does not match bundle season {league['season']}"
        )
    _positive_int(league["week"], "league.week")
    _positive_int(league["num_teams"], "league.num_teams")
    if league["num_teams"] % 2 != 0:
        raise ValueError("league.num_teams must be even so matchups can pair every team")

    team_keys = _validate_matchups(
        _list(data["matchups"], "matchups"),
        week=league["week"],
        num_teams=league["num_teams"],
    )
    _validate_players(_list(data["players"], "players"), team_keys)
    return WeeklyActualsBundle(data)


def _validate_matchups(matchups: list[Any], *, week: int, num_teams: int) -> set[str]:
    expected = num_teams // 2
    if len(matchups) != expected:
        raise ValueError(f"matchups must cover every team ({expected} pairings)")
    ids: set[str] = set()
    team_keys: set[str] = set()
    for i, value in enumerate(matchups):
        matchup = _mapping(value, f"matchups[{i}]")
        _exact_keys(matchup, {"matchup_id", "week", "teams"}, f"matchups[{i}]")
        matchup_id = _string(matchup["matchup_id"], f"matchups[{i}].matchup_id")
        if not matchup_id or matchup_id in ids:
            raise ValueError("matchup_id values must be unique nonempty strings")
        ids.add(matchup_id)
        if matchup["week"] != week:
            raise ValueError("every matchup week must equal league.week")
        teams = _list(matchup["teams"], f"matchups[{i}].teams")
        if len(teams) != 2:
            raise ValueError("every matchup must have exactly two teams")
        for j, team_value in enumerate(teams):
            team = _mapping(team_value, f"matchups[{i}].teams[{j}]")
            _exact_keys(team, {"team_key", "points"}, f"matchups[{i}].teams[{j}]")
            team_key = _string(team["team_key"], f"matchups[{i}].teams[{j}].team_key")
            if not team_key or team_key in team_keys:
                raise ValueError("matchup team_key values must be unique nonempty strings")
            team_keys.add(team_key)
            _finite(team["points"], f"matchups[{i}].teams[{j}].points")
    if len(team_keys) != num_teams:
        raise ValueError("matchups must cover every team exactly once")
    return team_keys


def _validate_players(players: list[Any], team_keys: set[str]) -> None:
    ids: set[str] = set()
    for i, value in enumerate(players):
        player = _mapping(value, f"players[{i}]")
        _exact_keys(
            player,
            {
                "yahoo_player_id",
                "yahoo_player_key",
                "name",
                "team_key",
                "selected_position",
                "points",
            },
            f"players[{i}]",
        )
        for field in (
            "yahoo_player_id",
            "yahoo_player_key",
            "name",
            "team_key",
            "selected_position",
        ):
            _string(player[field], f"players[{i}].{field}")
        if player["yahoo_player_id"] in ids:
            raise ValueError("Yahoo player IDs must be unique across actuals")
        ids.add(player["yahoo_player_id"])
        if player["team_key"] not in team_keys:
            raise ValueError("player team_key must appear on the scoreboard")
        _finite(player["points"], f"players[{i}].points")


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _positive_int(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _finite(value: object, name: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} has unknown or missing fields")


def _utc_timestamp(value: object, name: str) -> None:
    text = _string(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an RFC 3339 UTC timestamp") from exc
    if parsed.tzinfo != UTC:
        raise ValueError(f"{name} must be UTC")
