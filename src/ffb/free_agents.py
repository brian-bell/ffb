"""Closed FreeAgentsBundle v1 — the unrostered Yahoo player pool.

Yahoo exposes no API for free agents here, so the Grok bot reads the pool from
the live UI and posts it to the Worker. Sibling of ``WeeklyActualsBundle``:
extra keys fail, and the pool is a snapshot or Worker KV, never DuckDB or git.
Points and ranks are computed by the CLI; the bundle carries identity only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ffb import config, identity
from ffb.retro import league_snapshot_segment


@dataclass(frozen=True)
class FreeAgentsBundle:
    """Validated free-agent pool for one league."""

    data: dict[str, Any]

    @property
    def league(self) -> dict[str, Any]:
        return self.data["league"]

    @property
    def players(self) -> list[dict[str, Any]]:
        return self.data["players"]


# A pool scraped longer ago than this misses recent drops and is not published;
# it matches the dashboard's 5-day lineup/waivers stale limit.
POOL_MAX_AGE = timedelta(days=5)


def pool_age(bundle: FreeAgentsBundle, *, now: datetime) -> timedelta:
    """Time since the pool was scraped."""
    synced_at = datetime.fromisoformat(bundle.data["synced_at"].replace("Z", "+00:00"))
    return now - synced_at


def free_agents_snapshot_key(season: int, league_key: str | None = None) -> str:
    """Snapshot path of the latest pool; the pool is current-state, not per week."""
    return f"free_agents/{league_snapshot_segment(league_key)}{season}"


def resolve_free_agents(
    store: Any, bundle: FreeAgentsBundle
) -> tuple[dict[str, list[str]], list[str]]:
    """Map the pool to canonical keys: ``({player_key: slot eligibility}, unmatched names)``.

    Yahoo ids resolve through the crosswalk; team defenses by team code or Yahoo
    defense id. A player with no unique match is reported, never guessed, and
    cannot enter the ranking. ``store`` needs only ``resolve_batch``.
    """
    players = bundle.players
    lookup = store.resolve_batch("yahoo", [player["native_id"] for player in players])
    pool: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for player in players:
        position = player["primary_position"]
        defense = identity.resolve_roster_defense(
            position,
            player["native_id"],
            player["nfl_team"],
            player["name"],
            provider="yahoo",
        )
        if defense is not None:
            pool[defense[0]] = ["DEF"]
            continue
        hit = lookup.get(player["native_id"])
        if hit is None:
            unmatched.append(player["name"])
            continue
        pool[hit["player_key"]] = identity.merge_eligibility(
            hit.get("position") or position, position, player["eligible_positions"]
        )
    return pool, sorted(unmatched)


def parse_free_agents(
    payload: object, *, season: int, league_key: str | None = None
) -> FreeAgentsBundle:
    """Validate the closed schema-v1 free-agents contract before any write.

    With ``league_key``, the bundle's namespaced league must be that league, so
    one league's pool is never stored or ranked under another.
    """
    data = _mapping(payload, "bundle")
    _exact_keys(data, {"schema_version", "source", "synced_at", "league", "players"}, "bundle")
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
    for key in ("season", "week", "num_teams"):
        _positive_int(league[key], f"league.{key}")
    if league["season"] != season:
        raise ValueError(
            f"requested season {season} does not match bundle season {league['season']}"
        )
    if league_key is not None:
        bundle_league = config.namespaced_league_key(data["source"], league["league_key"])
        if bundle_league != league_key:
            raise ValueError(f"bundle league {bundle_league} does not match {league_key}")

    players = _list(data["players"], "players")
    if not players:
        raise ValueError("players must not be empty; an empty pool is a failed scrape")
    ids: set[str] = set()
    for i, value in enumerate(players):
        player = _mapping(value, f"players[{i}]")
        _exact_keys(
            player,
            {
                "native_id",
                "native_player_key",
                "name",
                "nfl_team",
                "primary_position",
                "eligible_positions",
            },
            f"players[{i}]",
        )
        for field in ("native_id", "native_player_key", "name", "primary_position"):
            _string(player[field], f"players[{i}].{field}")
        if player["nfl_team"] is not None:
            _string(player["nfl_team"], f"players[{i}].nfl_team")
        eligible = _list(player["eligible_positions"], f"players[{i}].eligible_positions")
        for j, position in enumerate(eligible):
            _string(position, f"players[{i}].eligible_positions[{j}]")
        if player["native_id"] in ids:
            raise ValueError("native player IDs must be unique across free agents")
        ids.add(player["native_id"])
    return FreeAgentsBundle(data)


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _positive_int(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} has unknown or missing fields")


# Mirrors the Worker's `utcTimestamp` and ``ffb.actuals``: a `T` separator and a
# literal `Z` or `+00:00` suffix, so one bundle is never valid on only one side.
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$")


def _utc_timestamp(value: object, name: str) -> None:
    text = _string(value, name)
    if not _UTC_TIMESTAMP.match(text):
        raise ValueError(f"{name} must be an RFC 3339 UTC timestamp (Z or +00:00)")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an RFC 3339 UTC timestamp") from exc
    if parsed.tzinfo != UTC:
        raise ValueError(f"{name} must be UTC")
