"""Yahoo Fantasy league source: authed fetch + pure raw -> LeagueBundle mapping.

Endpoints (OAuth2 bearer, ``format=json``)::

    GET https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}
    GET .../league/{league_key}/settings
    GET .../league/{league_key}/teams
    GET .../team/{team_key}/roster;week={week}

Yahoo's JSON has three quirks the pure mappers are defensive about, consistent
with the ESPN/FFC parser posture: everything is wrapped in ``fantasy_content``;
collections arrive as dicts keyed by stringified integers alongside a ``count``
key; and single logical objects arrive as positional arrays mixing dicts and
lists. The shapes here are modeled from documentation, not observed traffic —
ffb-1ct.2 corrects them against real responses.

``YahooLeagueSource`` is the live peer of ``FixtureLeagueSource``: every raw
pull goes through ``SnapshotCache`` so replays stay offline, and the merged
payload is validated by ``league.parse_bundle`` before anything downstream
sees it. Access tokens live only in request headers — never in snapshots,
logs, or the bundle.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from ffb import config, identity, paths, yahoo_auth
from ffb.league import LeagueBundle, parse_bundle

log = logging.getLogger(__name__)

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"
USER_AGENT = "ffb/0.1 (personal use)"

_NON_STARTING_SLOTS = {"BN", "IR", "IL"}


# --- thin authed fetch -------------------------------------------------------


def _get(client: httpx.Client, path: str, token: str) -> Any:
    url = f"{BASE_URL}{path}"
    log.info("api request provider=yahoo method=GET url=%s", url)
    response = client.get(
        url,
        params={"format": "json"},
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=30.0,
    )
    log.info("api response provider=yahoo status=%s", response.status_code)
    response.raise_for_status()
    return response.json()


def fetch_league(client: httpx.Client, league_key: str, token: str) -> Any:
    return _get(client, f"/league/{league_key}", token)


def fetch_settings(client: httpx.Client, league_key: str, token: str) -> Any:
    return _get(client, f"/league/{league_key}/settings", token)


def fetch_teams(client: httpx.Client, league_key: str, token: str) -> Any:
    return _get(client, f"/league/{league_key}/teams", token)


def fetch_roster(client: httpx.Client, team_key: str, week: int, token: str) -> Any:
    return _get(client, f"/team/{team_key}/roster;week={int(week)}", token)


def snapshot_key(league_key: str, resource: str) -> str:
    return f"yahoo/league_{league_key}_{resource}"


def roster_snapshot_key(team_key: str, week: int) -> str:
    return f"yahoo/roster_{team_key}_week{week}"


# --- pure raw -> bundle mapping ----------------------------------------------


def _content(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("fantasy_content"), dict):
        raise ValueError("Yahoo response is missing the fantasy_content wrapper")
    return raw["fantasy_content"]


def _fragments(value: Any, name: str) -> list[Any]:
    """Normalize Yahoo's positional-array-or-plain-object ambiguity to a list."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    raise ValueError(f"{name} must be a Yahoo positional array or object")


def _merge(value: Any, name: str = "fragment") -> dict[str, Any]:
    """Flatten a positional mixture of dicts and nested lists into one dict."""
    merged: dict[str, Any] = {}
    for fragment in _fragments(value, name):
        if isinstance(fragment, dict):
            merged.update(fragment)
        elif isinstance(fragment, list):
            merged.update(_merge(fragment, name))
    return merged


def _indexed(value: Any, name: str) -> list[Any]:
    """Items of a collection: ``{"0": ..., "1": ..., "count": n}`` or a list.

    Strict about completeness: a gap in the integer keys or a declared
    ``count`` that disagrees with the entries means a truncated collection,
    which must fail rather than pass as the complete roster/team/rule set.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        indices = sorted(int(key) for key in value if isinstance(key, str) and key.isdigit())
        if indices != list(range(len(indices))):
            raise ValueError(f"{name} has non-contiguous entries")
        declared = value.get("count")
        if declared is not None and _int(declared, f"{name}.count") != len(indices):
            raise ValueError(f"{name} count does not match its entries")
        return [value[str(i)] for i in indices]
    raise ValueError(f"{name} must be a Yahoo collection")


def _int(value: Any, name: str) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be an integer")


def _str(value: Any, name: str) -> str:
    """Require a nonempty string identifier (numeric ids are normalized).

    This parser is the validation boundary for refreshed provider data, so a
    null or structured value must fail here rather than be laundered into a
    valid-looking string like ``"None"`` by blind coercion.
    """
    if isinstance(value, str) and value:
        return value
    if type(value) is int:
        return str(value)
    raise ValueError(f"{name} must be a nonempty string")


def _flag(value: Any) -> bool:
    return str(value) == "1"


def parse_league_meta(raw: Any) -> dict[str, Any]:
    merged = _merge(_content(raw).get("league"), "league")
    try:
        return {
            "league_id": _str(merged["league_id"], "league.league_id"),
            "league_key": _str(merged["league_key"], "league.league_key"),
            "name": _str(merged["name"], "league.name"),
            "season": _int(merged["season"], "league.season"),
            "current_week": _int(merged["current_week"], "league.current_week"),
            "num_teams": _int(merged["num_teams"], "league.num_teams"),
        }
    except KeyError as exc:
        raise ValueError(f"Yahoo league metadata is missing {exc}") from exc


def parse_settings(raw: Any) -> dict[str, Any]:
    league = _merge(_content(raw).get("league"), "league")
    settings = _merge(league.get("settings"), "league.settings")

    # Roster slots and stat modifiers are strict: they are a small, closed,
    # critical set, and silently dropping one would let an incomplete lineup
    # shape or scoring config pass validation and misprice every player.
    slots: list[dict[str, Any]] = []
    positions = _indexed(settings.get("roster_positions"), "settings.roster_positions")
    for i, item in enumerate(positions):
        try:
            slot = _merge(item.get("roster_position"), "roster_position")
            position = _str(slot["position"], "roster_position.position")
            slots.append(
                {
                    "position": position,
                    "count": _int(slot["count"], "roster_position.count"),
                    "is_starting": position not in _NON_STARTING_SLOTS,
                }
            )
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"malformed Yahoo roster position [{i}]: {exc}") from exc

    names: dict[int, str] = {}
    categories = settings.get("stat_categories")
    if isinstance(categories, dict):
        for item in _indexed(categories.get("stats", []), "stat_categories.stats"):
            try:
                stat = _merge(item.get("stat"), "stat")
                stat_id = _int(stat["stat_id"], "stat.stat_id")
                label = stat.get("name") or stat.get("display_name")
                names[stat_id] = label if isinstance(label, str) else ""
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                log.warning("skip malformed Yahoo stat category: %s", exc)

    mapped: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    # Required: a settings payload without modifiers is a truncated or drifted
    # response, and accepting it would promote empty scoring rules over the
    # known-good snapshot while reads silently fall back to configured values.
    modifiers = settings.get("stat_modifiers")
    if not isinstance(modifiers, dict) or "stats" not in modifiers:
        raise ValueError("settings.stat_modifiers must hold a stats collection")
    for item in _indexed(modifiers["stats"], "stat_modifiers.stats"):
        try:
            stat = _merge(item.get("stat"), "stat")
            stat_id = _int(stat["stat_id"], "stat.stat_id")
            points = float(stat["value"])
            provider_name = names.get(stat_id) or f"stat {stat_id}"
            keys = config.YAHOO_STAT_MAP.get(stat_id)
            if keys is None:
                unmapped.append(
                    {
                        "points": points,
                        "provider_stat_id": str(stat_id),
                        "provider_name": provider_name,
                    }
                )
                continue
            for stat_key in keys:
                mapped.append(
                    {
                        "stat_key": stat_key,
                        "points": points,
                        # Fanned-out rules need distinct provider ids to
                        # honor the bundle's uniqueness contract.
                        "provider_stat_id": (
                            str(stat_id) if len(keys) == 1 else f"{stat_id}.{stat_key}"
                        ),
                        "provider_name": provider_name,
                    }
                )
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"malformed Yahoo stat modifier: {exc}") from exc

    provider_settings = {
        key: value for key, value in settings.items() if isinstance(value, (str, int, float, bool))
    }
    return {
        "roster_slots": slots,
        "scoring_rules": mapped,
        "unmapped_scoring_rules": unmapped,
        "provider_settings": provider_settings,
    }


def parse_teams(raw: Any) -> list[dict[str, Any]]:
    league = _merge(_content(raw).get("league"), "league")
    # Strict like settings and rosters: a silently skipped team would persist
    # a partial snapshot that every later non-refresh sync replays.
    teams: list[dict[str, Any]] = []
    for i, item in enumerate(_indexed(league.get("teams"), "league.teams")):
        try:
            team = _merge(item.get("team"), "team")
            managers: list[str] = []
            manager_login = False
            for entry in _indexed(team.get("managers", []), "team.managers"):
                manager = _merge(entry.get("manager"), "manager") if isinstance(entry, dict) else {}
                nickname = manager.get("nickname")
                if isinstance(nickname, str) and nickname:
                    managers.append(nickname)
                manager_login = manager_login or _flag(manager.get("is_current_login"))
            teams.append(
                {
                    "team_id": _str(team["team_id"], "team.team_id"),
                    "team_key": _str(team["team_key"], "team.team_key"),
                    "name": _str(team["name"], "team.name"),
                    "managers": managers,
                    "is_user_team": _flag(team.get("is_owned_by_current_login")) or manager_login,
                }
            )
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"malformed Yahoo team [{i}]: {exc}") from exc
    return teams


def parse_roster(raw: Any) -> dict[str, Any]:
    team = _merge(_content(raw).get("team"), "team")
    roster = team.get("roster")
    if not isinstance(roster, dict):
        raise ValueError("Yahoo team response is missing a roster")
    players_node = roster.get("0") if isinstance(roster.get("0"), dict) else roster
    # Required: an omitted players collection is a truncated response, and
    # accepting it as an empty roster would erase the stored week. Yahoo's
    # explicit zero-count form ({"count": 0}) still parses as legitimately
    # empty.
    if "players" not in players_node:
        raise ValueError("Yahoo roster response is missing its players collection")
    # Strict: a skipped player would be silently dropped from the atomically
    # replaced league state, so a malformed entry rejects the whole roster and
    # preserves the previously stored state instead.
    players: list[dict[str, Any]] = []
    for i, item in enumerate(_indexed(players_node["players"], "roster.players")):
        try:
            players.append(_parse_player(item))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"malformed Yahoo roster player [{i}]: {exc}") from exc
    try:
        return {
            "team_key": _str(team["team_key"], "roster.team_key"),
            "week": _int(roster["week"], "roster.week"),
            "players": players,
        }
    except KeyError as exc:
        raise ValueError(f"Yahoo roster is missing {exc}") from exc


def _parse_player(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or "player" not in item:
        raise ValueError("roster entry has no player object")
    merged = _merge(item["player"], "player")
    name = merged.get("name")
    full_name = name.get("full") if isinstance(name, dict) else name
    if not isinstance(full_name, str) or not full_name:
        raise ValueError("player has no name")

    abbr = merged.get("editorial_team_abbr")
    nfl_team = None
    if isinstance(abbr, str) and abbr.strip():
        nfl_team = identity.canonical_team(abbr) or abbr.strip().upper()

    primary = merged.get("primary_position") or merged.get("display_position")
    if not isinstance(primary, str) or not primary:
        raise ValueError("player has no primary position")
    selected = _merge(merged.get("selected_position", []), "selected_position").get("position")
    if not isinstance(selected, str) or not selected:
        raise ValueError("player has no selected position")

    eligible: list[str] = []
    for entry in _indexed(merged.get("eligible_positions", []), "player.eligible_positions"):
        position = entry.get("position") if isinstance(entry, dict) else entry
        if isinstance(position, str) and position:
            eligible.append(position)

    return {
        "yahoo_player_id": _str(merged["player_id"], "player.player_id"),
        "yahoo_player_key": _str(merged["player_key"], "player.player_key"),
        "name": full_name,
        "nfl_team": nfl_team,
        "primary_position": primary,
        "eligible_positions": eligible,
        "selected_position": selected,
    }


def map_bundle(
    *,
    meta: Any,
    settings: Any,
    teams: Any,
    rosters: list[Any],
    synced_at: str,
) -> dict[str, Any]:
    """Pure raw responses -> schema-v1 bundle payload for ``parse_bundle``."""
    return {
        "schema_version": 1,
        "source": "yahoo",
        "synced_at": synced_at,
        "league": parse_league_meta(meta),
        "settings": parse_settings(settings),
        "teams": parse_teams(teams),
        "rosters": [parse_roster(raw) for raw in rosters],
    }


# --- live source -------------------------------------------------------------


class YahooLeagueSource:
    """Live peer of ``FixtureLeagueSource`` behind the ``LeagueSource`` protocol.

    ``token_provider`` is called lazily inside each cache-miss fetch, so a
    fully snapshotted league replays offline without touching auth.
    """

    def __init__(
        self,
        league_key: str,
        cache: Any,
        token_provider: Callable[[], str],
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.league_key = league_key
        self.cache = cache
        self.token_provider = token_provider
        self.transport = transport

    def fetch(self, season: int, *, refresh: bool = False) -> LeagueBundle:
        staged: dict[str, Any] = {}
        cached_keys: list[str] = []
        with httpx.Client(transport=self.transport) as client:

            def pull(key: str, fetch_fn: Callable[[], Any]) -> Any:
                if not refresh and self.cache.has(key):
                    cached_keys.append(key)
                    return self.cache.get_json(key, fetch_fn)
                data = fetch_fn()
                staged[key] = data
                return data

            meta = pull(
                snapshot_key(self.league_key, "meta"),
                lambda: fetch_league(client, self.league_key, self.token_provider()),
            )
            settings = pull(
                snapshot_key(self.league_key, "settings"),
                lambda: fetch_settings(client, self.league_key, self.token_provider()),
            )
            teams_raw = pull(
                snapshot_key(self.league_key, "teams"),
                lambda: fetch_teams(client, self.league_key, self.token_provider()),
            )
            week = parse_league_meta(meta)["current_week"]
            rosters = [
                pull(
                    roster_snapshot_key(team["team_key"], week),
                    lambda key=team["team_key"]: fetch_roster(
                        client, key, week, self.token_provider()
                    ),
                )
                for team in parse_teams(teams_raw)
            ]
        payload = map_bundle(
            meta=meta,
            settings=settings,
            teams=teams_raw,
            rosters=rosters,
            synced_at=self._synced_at(cached_keys, fetched_any=bool(staged)),
        )
        bundle = parse_bundle(payload, season=season)
        # Promote the refreshed raw pulls only after the whole bundle
        # validates: a fetch or mapping failure leaves every known-good
        # snapshot untouched, never a partially updated set. Owner-only
        # permissions because these responses were fetched under the user's
        # OAuth grant (private league membership and rosters).
        #
        # Deliberate tradeoff: promotion is per-file, so a hard kill inside
        # this loop can mix generations. Every file is still individually
        # valid, parse_bundle cross-checks teams/rosters/weeks on replay, and
        # `--refresh` re-pulls the disposable cache; a transactional
        # generation manifest is not worth the complexity here.
        for key, data in staged.items():
            self.cache.put_json(key, data, mode=0o600)
        return bundle

    def _synced_at(self, cached_keys: list[str], *, fetched_any: bool) -> str:
        """Age of the oldest raw pull backing this bundle.

        Snapshot mtime records when each replayed resource was actually
        fetched, so a cache replay reports the data's true age instead of
        stamping stale state as freshly synchronized.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        stamps = [
            meta.modified_at for meta in map(self.cache.metadata, cached_keys) if meta is not None
        ]
        if fetched_any:
            stamps.append(now)
        return min(stamps) if stamps else now


def league_source_from_env(
    cache: Any, environ: Mapping[str, str] = os.environ
) -> YahooLeagueSource:
    """Build the live source from env config; raises ``YahooAuthError`` if unset."""
    league_key = environ.get("FFB_YAHOO_LEAGUE_KEY")
    if not league_key:
        raise yahoo_auth.YahooAuthError(
            "missing Yahoo league configuration: set FFB_YAHOO_LEAGUE_KEY (e.g. 461.l.<league_id>)"
        )

    def token_provider() -> str:
        # Resolved lazily so a fully snapshotted league replays without any
        # OAuth configuration; missing credentials only fail on a cache miss.
        credentials = yahoo_auth.YahooCredentials.from_env(environ)
        auth = yahoo_auth.YahooAuth(
            credentials, yahoo_auth.YahooTokenStore(paths.yahoo_token_path())
        )
        return auth.access_token()

    return YahooLeagueSource(league_key=league_key, cache=cache, token_provider=token_provider)
