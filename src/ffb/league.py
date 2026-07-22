"""Provider-neutral, fixture-backed Yahoo league state contract."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class LeagueSource(Protocol):
    def fetch(self, season: int, *, refresh: bool = False) -> LeagueBundle: ...


@dataclass(frozen=True)
class LeagueBundle:
    """Validated provider-neutral league state; no provider model leaks past here."""

    data: dict[str, Any]

    @property
    def league(self) -> dict[str, Any]:
        return self.data["league"]

    @property
    def settings(self) -> dict[str, Any]:
        return self.data["settings"]

    @property
    def teams(self) -> list[dict[str, Any]]:
        return self.data["teams"]

    @property
    def rosters(self) -> list[dict[str, Any]]:
        return self.data["rosters"]


class FixtureLeagueSource:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def fetch(self, season: int, *, refresh: bool = False) -> LeagueBundle:
        del refresh
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid league fixture {self.path}: {exc}") from exc
        return parse_bundle(payload, season=season)


def parse_bundle(payload: object, *, season: int) -> LeagueBundle:
    """Validate the closed schema-v1 fixture contract before any store write."""
    data = _mapping(payload, "bundle")
    _exact_keys(
        data,
        {"schema_version", "source", "synced_at", "league", "settings", "teams", "rosters"},
        "bundle",
    )
    if data["schema_version"] != 1:
        raise ValueError("bundle.schema_version must be 1")
    if data["source"] not in ("fixture", "yahoo"):
        raise ValueError("bundle.source must be fixture or yahoo")
    _utc_timestamp(data["synced_at"], "bundle.synced_at")
    league = _mapping(data["league"], "league")
    _exact_keys(
        league, {"league_id", "league_key", "name", "season", "current_week", "num_teams"}, "league"
    )
    for key in ("league_id", "league_key", "name"):
        _string(league[key], f"league.{key}")
    _positive_int(league["season"], "league.season")
    if league["season"] != season:
        raise ValueError(
            f"requested season {season} does not match bundle season {league['season']}"
        )
    _positive_int(league["current_week"], "league.current_week")
    _positive_int(league["num_teams"], "league.num_teams")

    settings = _mapping(data["settings"], "settings")
    _exact_keys(
        settings,
        {"roster_slots", "scoring_rules", "unmapped_scoring_rules", "provider_settings"},
        "settings",
    )
    _list(settings["roster_slots"], "settings.roster_slots")
    _list(settings["scoring_rules"], "settings.scoring_rules")
    _list(settings["unmapped_scoring_rules"], "settings.unmapped_scoring_rules")
    _mapping(settings["provider_settings"], "settings.provider_settings")
    _validate_slots(settings["roster_slots"])
    _validate_rules(settings["scoring_rules"], settings["unmapped_scoring_rules"])

    teams = _list(data["teams"], "teams")
    if len(teams) != league["num_teams"]:
        raise ValueError("league.num_teams must equal number of teams")
    keys: set[str] = set()
    ids: set[str] = set()
    for i, team_value in enumerate(teams):
        team = _mapping(team_value, f"teams[{i}]")
        _exact_keys(
            team, {"team_id", "team_key", "name", "managers", "is_user_team"}, f"teams[{i}]"
        )
        for field in ("team_id", "team_key", "name"):
            _string(team[field], f"teams[{i}].{field}")
        _strings(team["managers"], f"teams[{i}].managers")
        if type(team["is_user_team"]) is not bool:
            raise ValueError(f"teams[{i}].is_user_team must be boolean")
        if team["team_id"] in ids or team["team_key"] in keys:
            raise ValueError("team IDs and keys must be unique")
        ids.add(team["team_id"])
        keys.add(team["team_key"])

    rosters = _list(data["rosters"], "rosters")
    if len(rosters) != len(keys):
        raise ValueError("every team must have exactly one current-week roster")
    roster_keys: set[str] = set()
    player_ids: set[str] = set()
    for i, roster_value in enumerate(rosters):
        roster = _mapping(roster_value, f"rosters[{i}]")
        _exact_keys(roster, {"team_key", "week", "players"}, f"rosters[{i}]")
        _string(roster["team_key"], f"rosters[{i}].team_key")
        if roster["week"] != league["current_week"]:
            raise ValueError("every roster week must equal league.current_week")
        if roster["team_key"] in roster_keys:
            raise ValueError("every team must have exactly one current-week roster")
        roster_keys.add(roster["team_key"])
        for j, player_value in enumerate(_list(roster["players"], f"rosters[{i}].players")):
            player = _mapping(player_value, f"rosters[{i}].players[{j}]")
            _exact_keys(
                player,
                {
                    "yahoo_player_id",
                    "yahoo_player_key",
                    "name",
                    "nfl_team",
                    "primary_position",
                    "eligible_positions",
                    "selected_position",
                },
                f"rosters[{i}].players[{j}]",
            )
            for field in (
                "yahoo_player_id",
                "yahoo_player_key",
                "name",
                "primary_position",
                "selected_position",
            ):
                _string(player[field], f"rosters[{i}].players[{j}].{field}")
            if player["nfl_team"] is not None:
                _string(player["nfl_team"], f"rosters[{i}].players[{j}].nfl_team")
            _strings(player["eligible_positions"], f"rosters[{i}].players[{j}].eligible_positions")
            if player["yahoo_player_id"] in player_ids:
                raise ValueError("Yahoo player IDs must be unique across league rosters")
            player_ids.add(player["yahoo_player_id"])
    if roster_keys != keys:
        raise ValueError("roster team keys must equal the team-key set")
    return LeagueBundle(data)


def _validate_slots(slots: list[object]) -> None:
    positions: set[str] = set()
    for i, value in enumerate(slots):
        slot = _mapping(value, f"roster_slots[{i}]")
        _exact_keys(slot, {"position", "count", "is_starting"}, f"roster_slots[{i}]")
        position = _string(slot["position"], f"roster_slots[{i}].position")
        if not position or position in positions:
            raise ValueError("roster slot positions must be unique nonempty strings")
        positions.add(position)
        if type(slot["count"]) is not int or slot["count"] < 0:
            raise ValueError("roster slot counts must be nonnegative integers")
        if type(slot["is_starting"]) is not bool:
            raise ValueError("roster slot is_starting must be boolean")


def _validate_rules(mapped: list[object], unmapped: list[object]) -> None:
    stat_keys: set[str] = set()
    provider_ids: set[str] = set()
    for i, value in enumerate(mapped):
        rule = _mapping(value, f"scoring_rules[{i}]")
        _exact_keys(
            rule, {"stat_key", "points", "provider_stat_id", "provider_name"}, f"scoring_rules[{i}]"
        )
        stat_keys.add(_string(rule["stat_key"], f"scoring_rules[{i}].stat_key"))
        provider_ids.add(_string(rule["provider_stat_id"], f"scoring_rules[{i}].provider_stat_id"))
        _string(rule["provider_name"], f"scoring_rules[{i}].provider_name")
        _finite(rule["points"], f"scoring_rules[{i}].points")
    if len(stat_keys) != len(mapped) or len(provider_ids) != len(mapped):
        raise ValueError("scoring rule keys and provider stat IDs must be unique")
    for i, value in enumerate(unmapped):
        rule = _mapping(value, f"unmapped_scoring_rules[{i}]")
        _exact_keys(
            rule, {"points", "provider_stat_id", "provider_name"}, f"unmapped_scoring_rules[{i}]"
        )
        provider_id = _string(
            rule["provider_stat_id"], f"unmapped_scoring_rules[{i}].provider_stat_id"
        )
        if provider_id in provider_ids:
            raise ValueError("provider stat IDs must be unique across scoring rules")
        provider_ids.add(provider_id)
        _string(rule["provider_name"], f"unmapped_scoring_rules[{i}].provider_name")
        _finite(rule["points"], f"unmapped_scoring_rules[{i}].points")


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


def _strings(value: object, name: str) -> list[str]:
    return [_string(v, name) for v in _list(value, name)]


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
