"""Sleeper league adapter: unauthed fetch + pure raw -> sit/start mapping.

Thin peer of ``YahooLeagueSource`` for Brian's second league
(``sleeper:1395854363380965376``). This spike maps league/rosters/users/state
in memory for ``ffb lineup --league sleeper`` and must not write DuckDB
``league_*`` or POST Worker KV. Yahoo remains the occupant of those stores.

Endpoints (no auth)::

    GET https://api.sleeper.app/v1/league/{league_id}
    GET https://api.sleeper.app/v1/league/{league_id}/rosters
    GET https://api.sleeper.app/v1/league/{league_id}/users
    GET https://api.sleeper.app/v1/state/nfl

``yahoo_player_id`` / ``yahoo_player_key`` on mapped roster rows are Sleeper
native-id aliases required by the closed lineup-snapshot player shape. They
are not Yahoo identities and must be resolved with ``resolve_batch("sleeper")``,
never ``yahoo_id``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from ffb import config, identity
from ffb.league import LeagueBundle, parse_bundle
from ffb.sources.sleeper import USER_AGENT

log = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.app/v1"
_NON_STARTING_SLOTS = frozenset({"BN", "IR", "IL", "TAXI"})
_UNSUPPORTED_SLOTS = frozenset(
    {"SUPER_FLEX", "Q/W/R/T", "REC_FLEX", "IDP", "DL", "LB", "DB", "IDP_FLEX"}
)
_SLOT_MAP = {"FLEX": "W/R/T"}


class SleeperLeagueError(Exception):
    """Missing env config or an unusable Sleeper league payload."""


# --- thin fetch --------------------------------------------------------------


def _get(client: httpx.Client, path: str) -> Any:
    url = f"{BASE_URL}{path}"
    log.info("api request provider=sleeper method=GET url=%s", url)
    response = client.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30.0,
    )
    log.info("api response provider=sleeper status=%s", response.status_code)
    response.raise_for_status()
    return response.json()


def fetch_league(client: httpx.Client, league_id: str) -> Any:
    return _get(client, f"/league/{league_id}")


def fetch_rosters(client: httpx.Client, league_id: str) -> Any:
    return _get(client, f"/league/{league_id}/rosters")


def fetch_users(client: httpx.Client, league_id: str) -> Any:
    return _get(client, f"/league/{league_id}/users")


def fetch_state(client: httpx.Client) -> Any:
    return _get(client, "/state/nfl")


def snapshot_key(league_id: str, resource: str) -> str:
    return f"sleeper/league_{league_id}_{resource}"


def state_snapshot_key() -> str:
    return "sleeper/state_nfl"


# --- pure raw -> sit/start mapping -------------------------------------------


def sleeper_league_key(league_id: str) -> str:
    return f"sleeper:{league_id}"


def map_slot(position: str) -> str:
    """Map a Sleeper roster slot onto the labels ``compare_lineup`` already knows."""
    if position in _UNSUPPORTED_SLOTS:
        raise ValueError(f"unsupported Sleeper roster slot {position!r}")
    return _SLOT_MAP.get(position, position)


def collapse_roster_positions(positions: list[str]) -> list[dict[str, Any]]:
    """Count unique slots after FLEX → W/R/T. Reject SUPER_FLEX / IDP."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for raw in positions:
        if not isinstance(raw, str) or not raw:
            raise ValueError("roster_positions must be nonempty strings")
        slot = map_slot(raw)
        if slot not in counts:
            order.append(slot)
            counts[slot] = 0
        counts[slot] += 1
    return [
        {"position": slot, "count": counts[slot], "is_starting": slot not in _NON_STARTING_SLOTS}
        for slot in order
    ]


def parse_scoring_settings(raw: Any) -> dict[str, Any]:
    """Map Sleeper scoring_settings to weights. Fail loud on unsupported bonuses."""
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Sleeper scoring_settings must be a nonempty object")
    weights: dict[str, float] = {}
    rules: list[dict[str, Any]] = []
    for provider_key, raw_points in raw.items():
        if not isinstance(provider_key, str) or not provider_key:
            raise ValueError("scoring_settings keys must be nonempty strings")
        try:
            points = float(raw_points)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"scoring_settings.{provider_key} must be numeric") from exc
        if not points:
            continue
        mapped = config.SLEEPER_STAT_MAP.get(provider_key)
        if mapped is None:
            raise ValueError(
                f"unsupported Sleeper scoring setting {provider_key!r}={points}; "
                "refusing to fall back to Yahoo LEAGUE_SCORING"
            )
        for stat_key in mapped:
            existing = weights.get(stat_key)
            if existing is not None and existing != points:
                raise ValueError(
                    f"Sleeper scoring maps {provider_key!r} to {stat_key}={points} "
                    f"but that key is already {existing}"
                )
            if existing is None:
                weights[stat_key] = points
                rules.append(
                    {
                        "stat_key": stat_key,
                        "points": points,
                        "provider_stat_id": (
                            provider_key if len(mapped) == 1 else f"{provider_key}.{stat_key}"
                        ),
                        "provider_name": provider_key,
                    }
                )
    if not weights:
        raise ValueError("Sleeper scoring_settings produced no nonzero mapped rules")
    return {
        "scoring_rules": rules,
        "weights": weights,
        "scoring": config.ScoringConfig(dict(weights)),
    }


def parse_nfl_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Sleeper NFL state must be an object")
    try:
        week = raw["week"]
        season = raw.get("season") or raw.get("league_season")
        return {
            "week": week if type(week) is int else int(week),
            "season": season if type(season) is int else int(season),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Sleeper NFL state is missing week/season: {exc}") from exc


def parse_league_meta(raw: Any, *, current_week: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Sleeper league must be an object")
    try:
        league_id = _str(raw["league_id"], "league.league_id")
        season = raw["season"]
        return {
            "league_id": league_id,
            "league_key": sleeper_league_key(league_id),
            "name": _str(raw["name"], "league.name"),
            "season": season if type(season) is int else int(season),
            "current_week": int(current_week),
            "num_teams": _int(
                raw.get("total_rosters") or raw.get("settings", {}).get("num_teams"),
                "league.total_rosters",
            ),
            "roster_positions": list(raw["roster_positions"]),
            "scoring_settings": raw["scoring_settings"],
            "settings": raw.get("settings") if isinstance(raw.get("settings"), dict) else {},
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Sleeper league metadata is unusable: {exc}") from exc


def parse_users(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("Sleeper users must be a list")
    users: dict[str, dict[str, Any]] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"malformed Sleeper user [{i}]")
        user_id = _str(item.get("user_id"), f"users[{i}].user_id")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        team_name = metadata.get("team_name")
        display = item.get("display_name") or item.get("username") or user_id
        users[user_id] = {
            "user_id": user_id,
            "username": item.get("username") if isinstance(item.get("username"), str) else None,
            "display_name": display if isinstance(display, str) and display else user_id,
            "team_name": team_name if isinstance(team_name, str) and team_name else None,
        }
    return users


def parse_teams(
    rosters: Any,
    users: Mapping[str, dict[str, Any]],
    *,
    user_id: str,
    league_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(rosters, list):
        raise ValueError("Sleeper rosters must be a list")
    wanted = _str(user_id, "user_id")
    teams: list[dict[str, Any]] = []
    for i, roster in enumerate(rosters):
        if not isinstance(roster, dict):
            raise ValueError(f"malformed Sleeper roster [{i}]")
        roster_id = _str(roster.get("roster_id"), f"rosters[{i}].roster_id")
        owner_id = _optional_str(roster.get("owner_id"))
        user = users.get(owner_id or "")
        name = (
            (user or {}).get("team_name")
            or (user or {}).get("display_name")
            or f"Roster {roster_id}"
        )
        manager = (user or {}).get("display_name") or (user or {}).get("username")
        teams.append(
            {
                "team_id": roster_id,
                "team_key": f"{sleeper_league_key(league_id)}.t.{roster_id}",
                "name": name,
                "managers": [manager] if manager else [],
                "is_user_team": owner_id == wanted,
            }
        )
    return teams


def starting_slots(roster_positions: list[str]) -> list[str]:
    return [
        map_slot(slot) for slot in roster_positions if map_slot(slot) not in _NON_STARTING_SLOTS
    ]


def parse_roster(
    roster: Any,
    *,
    roster_positions: list[str],
    week: int,
    league_id: str,
    players_by_id: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(roster, dict):
        raise ValueError("Sleeper roster must be an object")
    roster_id = _str(roster.get("roster_id"), "roster.roster_id")
    starters = roster.get("starters") or []
    if not isinstance(starters, list):
        raise ValueError("roster.starters must be a list")
    player_ids = roster.get("players") or []
    if not isinstance(player_ids, list):
        raise ValueError("roster.players must be a list")
    reserve = roster.get("reserve") or []
    if reserve is None:
        reserve = []
    if not isinstance(reserve, list):
        raise ValueError("roster.reserve must be a list")
    taxi = roster.get("taxi")
    if taxi:
        raise ValueError("Sleeper taxi players are unsupported; they would be treated as BN")
    slots = starting_slots(roster_positions)
    if len(starters) != len(slots):
        raise ValueError(
            f"roster starters length {len(starters)} does not match "
            f"{len(slots)} starting roster_positions"
        )
    selected: dict[str, str] = {}
    for pid, slot in zip(starters, slots, strict=True):
        if _vacant_starter(pid):
            continue
        selected[_str(pid, "starter")] = slot
    reserved = {_str(pid, "reserve") for pid in reserve if not _vacant_starter(pid)}
    lookup = players_by_id or {}
    players: list[dict[str, Any]] = []
    for raw_id in player_ids:
        pid = _str(raw_id, "player_id")
        if pid in selected:
            chosen = selected[pid]
        elif pid in reserved:
            chosen = "IR"
        else:
            chosen = "BN"
        players.append(_parse_player(pid, lookup.get(pid), selected_position=chosen))
    return {
        "team_key": f"{sleeper_league_key(league_id)}.t.{roster_id}",
        "week": int(week),
        "players": players,
    }


def _parse_player(player_id: str, raw: Any, *, selected_position: str) -> dict[str, Any]:
    meta = raw if isinstance(raw, dict) else {}
    first = meta.get("first_name") if isinstance(meta.get("first_name"), str) else ""
    last = meta.get("last_name") if isinstance(meta.get("last_name"), str) else ""
    name = f"{first} {last}".strip() or player_id
    raw_position = meta.get("position")
    raw_team = meta.get("team")
    team_from_id = identity.canonical_team(player_id)
    if isinstance(raw_position, str) and raw_position.strip().upper() in {"DEF", "DST"}:
        position = "DEF"
    elif team_from_id and not (
        isinstance(raw_position, str) and raw_position in config.FANTASY_POSITIONS
    ):
        position = "DEF"
    elif isinstance(raw_position, str) and raw_position:
        position = "K" if raw_position == "PK" else raw_position
    else:
        # Crosswalk resolution fills this later; do not guess a skill position.
        position = "UNK"
    team_code = raw_team if isinstance(raw_team, str) and raw_team else player_id
    if position == "DEF":
        defense = identity.canonical_defense_key(
            "DEF", team_code
        ) or identity.canonical_defense_key("DEF", player_id)
        if defense is None:
            raise ValueError(f"Sleeper DEF {player_id} does not canonicalize")
        nfl_team = defense[1]
    else:
        nfl_team = identity.canonical_team(team_code) or (
            team_code.strip().upper() if isinstance(team_code, str) and team_code.strip() else None
        )
    return {
        # Bundle-contract alias: the player shape requires yahoo_player_id.
        # Value is the Sleeper native id, not a Yahoo id, until the
        # provider-neutral rename lands.
        "yahoo_player_id": player_id,
        "yahoo_player_key": f"sleeper:{player_id}",
        "name": name,
        "nfl_team": nfl_team,
        "primary_position": position,
        "eligible_positions": _eligible(position),
        "selected_position": selected_position,
    }


def _eligible(position: str) -> list[str]:
    if position == "RB":
        return ["RB", "W/R/T"]
    if position in {"WR", "TE"}:
        return [position, "W/R/T"]
    return [position]


def resolve_sleeper_roster_rows(store: Any, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach canonical keys via ``resolve_batch("sleeper")``. DEF uses ``def:<team>``."""
    native_ids = [player["yahoo_player_id"] for player in players]
    lookup = store.resolve_batch("sleeper", native_ids)
    rows: list[dict[str, Any]] = []
    for player in players:
        native_id = player["yahoo_player_id"]
        defense = identity.canonical_defense_key(
            player.get("primary_position"), player.get("nfl_team")
        )
        if defense is not None:
            player_key, team = defense
            rows.append(
                {
                    **player,
                    "player_key": player_key,
                    "matched": True,
                    "full_name": player["name"],
                    "position": "DEF",
                    "team": team,
                    "nfl_team": team,
                    "primary_position": "DEF",
                    "eligible_positions": _eligible("DEF"),
                }
            )
            continue
        hit = lookup.get(native_id)
        if hit is not None:
            xw_pos = hit["position"] if hit["position"] in config.FANTASY_POSITIONS else None
            if xw_pos is None and hit["position"] == "PK":
                xw_pos = "K"
            position = xw_pos or player["primary_position"]
            rows.append(
                {
                    **player,
                    "player_key": hit["player_key"],
                    "matched": True,
                    "full_name": hit["full_name"] or player["name"],
                    "position": position,
                    "team": hit["team"] or player["nfl_team"],
                    "nfl_team": hit["team"] or player["nfl_team"],
                    "primary_position": position,
                    "eligible_positions": _eligible(position),
                }
            )
        else:
            position = player["primary_position"]
            rows.append(
                {
                    **player,
                    "player_key": f"sleeper:{native_id}",
                    "matched": False,
                    "full_name": player["name"],
                    "position": position,
                    "team": player["nfl_team"],
                    "eligible_positions": _eligible(position),
                }
            )
    return rows


def map_state(
    *,
    league: Any,
    rosters: Any,
    users: Any,
    state: Any,
    user_id: str,
    season: int,
    synced_at: str,
    players_by_id: Mapping[str, Any] | None = None,
) -> LeagueBundle:
    """Pure raw responses -> the provider-neutral ``LeagueBundle`` contract.

    Shape, uniqueness, and season/week cross-checks belong to ``parse_bundle``;
    only Sleeper-specific rules (slot support, taxi, user-team uniqueness, and
    fail-loud scoring) are enforced here.
    """
    nfl = parse_nfl_state(state)
    meta = parse_league_meta(league, current_week=nfl["week"])
    slots = collapse_roster_positions(meta["roster_positions"])
    scoring = parse_scoring_settings(meta["scoring_settings"])
    user_map = parse_users(users)
    teams = parse_teams(rosters, user_map, user_id=user_id, league_id=meta["league_id"])
    if sum(team["is_user_team"] for team in teams) != 1:
        raise ValueError(
            f"Sleeper user_id {user_id} must own exactly one roster (is_user_team uniqueness)"
        )
    settings = meta["settings"]
    taxi_slots = settings.get("taxi_slots")
    if taxi_slots not in (None, 0):
        raise ValueError(
            f"Sleeper taxi_slots={taxi_slots!r} is unsupported "
            "(taxi players would be treated as BN)"
        )
    payload = {
        "schema_version": 1,
        "source": "sleeper",
        "synced_at": synced_at,
        "league": {
            "league_id": meta["league_id"],
            "league_key": meta["league_key"],
            "name": meta["name"],
            "season": meta["season"],
            "current_week": meta["current_week"],
            "num_teams": meta["num_teams"],
        },
        "settings": {
            "roster_slots": slots,
            "scoring_rules": scoring["scoring_rules"],
            # Sleeper fails loud on unmapped nonzero settings rather than
            # scoring a league with rules it silently dropped, so this list is
            # always empty. See parse_scoring_settings.
            "unmapped_scoring_rules": [],
            "provider_settings": {
                key: settings[key]
                for key in ("playoff_week_start", "reserve_slots", "taxi_slots", "max_keepers")
                if key in settings
            },
        },
        "teams": teams,
        "rosters": [
            parse_roster(
                roster,
                roster_positions=meta["roster_positions"],
                week=meta["current_week"],
                league_id=meta["league_id"],
                players_by_id=players_by_id,
            )
            for roster in rosters
        ],
    }
    return parse_bundle(payload, season=season)


# --- live source -------------------------------------------------------------


class SleeperLeagueSource:
    """Live peer of ``YahooLeagueSource`` that never writes ``league_*``."""

    def __init__(
        self,
        league_id: str,
        user_id: str,
        cache: Any,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.league_id = league_id
        self.user_id = user_id
        self.cache = cache
        self.transport = transport

    def fetch(
        self,
        season: int,
        *,
        offline: bool = False,
        players_by_id: Mapping[str, Any] | None = None,
    ) -> LeagueBundle:
        """Pull live league state, or replay the last snapshots when ``offline``.

        Rosters and the NFL week are current-state endpoints whose snapshot keys
        carry no week, so a cached replay silently serves last week's starters
        and week number. Sit/start therefore refetches every run by default;
        snapshots exist for ``--offline`` and for post-mortem, not as a cache.
        """
        from ffb.snapshot import SnapshotPolicy

        policy = SnapshotPolicy.OFFLINE if offline else SnapshotPolicy.REFRESH
        staged: dict[str, Any] = {}
        cached_keys: list[str] = []
        with httpx.Client(transport=self.transport) as client:

            def pull(key: str, fetch_fn: Callable[[], Any]) -> Any:
                if policy is SnapshotPolicy.OFFLINE:
                    cached_keys.append(key)
                    return self.cache.get_json(key, fetch_fn, policy=policy)
                data = fetch_fn()
                staged[key] = data
                return data

            league = pull(
                snapshot_key(self.league_id, "league"),
                lambda: fetch_league(client, self.league_id),
            )
            rosters = pull(
                snapshot_key(self.league_id, "rosters"),
                lambda: fetch_rosters(client, self.league_id),
            )
            users = pull(
                snapshot_key(self.league_id, "users"),
                lambda: fetch_users(client, self.league_id),
            )
            state = pull(state_snapshot_key(), lambda: fetch_state(client))
        lookup = players_by_id
        if lookup is None and self.cache.has("sleeper/players_nfl"):
            cached = self.cache.read_json("sleeper/players_nfl")
            lookup = cached if isinstance(cached, dict) else None
        mapped = map_state(
            league=league,
            rosters=rosters,
            users=users,
            state=state,
            user_id=self.user_id,
            season=season,
            synced_at=self._synced_at(cached_keys),
            players_by_id=lookup,
        )
        for key, data in staged.items():
            self.cache.put_json(key, data)
        return mapped

    def _synced_at(self, cached_keys: list[str]) -> str:
        """Age of the state behind this bundle.

        A run is either wholly live or wholly replayed, so this is ``now`` for a
        live pull and the oldest replayed snapshot's mtime under ``--offline``.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        stamps = [
            meta.modified_at for meta in map(self.cache.metadata, cached_keys) if meta is not None
        ]
        return min(stamps) if stamps else now


def league_source_from_env(
    cache: Any, environ: Mapping[str, str] = os.environ
) -> SleeperLeagueSource:
    league_id = environ.get("FFB_SLEEPER_LEAGUE_ID")
    user_id = environ.get("FFB_SLEEPER_USER_ID")
    missing = [
        name
        for name, value in (
            ("FFB_SLEEPER_LEAGUE_ID", league_id),
            ("FFB_SLEEPER_USER_ID", user_id),
        )
        if not value
    ]
    if missing:
        raise SleeperLeagueError(
            "missing Sleeper league configuration: set " + " and ".join(missing)
        )
    return SleeperLeagueSource(league_id=league_id, user_id=user_id, cache=cache)


def _str(value: Any, name: str) -> str:
    if isinstance(value, str) and value:
        return value
    if type(value) is int:
        return str(value)
    raise ValueError(f"{name} must be a nonempty string")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    if type(value) is int:
        return str(value)
    raise ValueError("owner_id must be a nonempty string")


def _int(value: Any, name: str) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be an integer")


def _vacant_starter(value: Any) -> bool:
    return value in (None, "", 0, "0")
