"""Weekly retro: join sit/start advice snapshots to weekly actuals.

Pure compute. The CLI owns snapshot I/O; this module never touches DuckDB,
KV, or the filesystem. Identity is ``yahoo_player_id`` only — never name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ffb.actuals import WeeklyActualsBundle
from ffb.lineup import is_starter

LINEUP_SNAPSHOT_KIND = "lineup_recommendation"
LINEUP_SNAPSHOT_KEYS = {
    "schema_version",
    "kind",
    "season",
    "week",
    "generated_at",
    "team_key",
    "team_name",
    "roster_slots",
    "players",
    "report",
}
_SNAPSHOT_PLAYER_KEYS = {
    "yahoo_player_id",
    "yahoo_player_key",
    "name",
    "position",
    "team",
    "selected_position",
    "projection_key",
    "player_key",
    "points",
    "source_points",
    "n",
    "matched",
}


def lineup_snapshot_key(season: int, week: int) -> str:
    return f"lineup/{season}_week{week}"


def actuals_snapshot_key(season: int, week: int) -> str:
    return f"actuals/{season}_week{week}"


def build_lineup_snapshot(
    *,
    season: int,
    week: int,
    generated_at: str,
    team_key: str,
    team_name: str,
    roster_slots: dict[str, int],
    players: list[dict[str, Any]],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Serialize one sit/start run as the retro replay boundary."""
    return parse_lineup_snapshot(
        {
            "schema_version": 1,
            "kind": LINEUP_SNAPSHOT_KIND,
            "season": season,
            "week": week,
            "generated_at": generated_at,
            "team_key": team_key,
            "team_name": team_name,
            "roster_slots": dict(roster_slots),
            "players": [_snapshot_player(player) for player in players],
            "report": {
                "current": [_identity_row(row) for row in report["current"]],
                "optimal": [_identity_row(row) for row in report["optimal"]],
                "start": [_identity_row(row) for row in report["start"]],
                "sit": [_identity_row(row) for row in report["sit"]],
                "current_total": report["current_total"],
                "optimal_total": report["optimal_total"],
                "delta": report["delta"],
            },
        }
    )


def parse_lineup_snapshot(payload: object) -> dict[str, Any]:
    """Validate a lineup-recommendation snapshot before retro joins it."""
    if not isinstance(payload, dict):
        raise ValueError("lineup snapshot must be an object")
    if set(payload) != LINEUP_SNAPSHOT_KEYS:
        raise ValueError("lineup snapshot has unknown or missing fields")
    if payload.get("schema_version") != 1:
        raise ValueError("lineup snapshot schema_version must be 1")
    if payload.get("kind") != LINEUP_SNAPSHOT_KIND:
        raise ValueError("lineup snapshot kind must be lineup_recommendation")
    for key in ("season", "week"):
        if type(payload[key]) is not int or payload[key] <= 0:
            raise ValueError(f"lineup snapshot {key} must be a positive integer")
    for key in ("generated_at", "team_key", "team_name"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValueError(f"lineup snapshot {key} must be a nonempty string")
    if not isinstance(payload["roster_slots"], dict):
        raise ValueError("lineup snapshot roster_slots must be an object")
    if not isinstance(payload["players"], list):
        raise ValueError("lineup snapshot players must be a list")
    if not isinstance(payload["report"], dict):
        raise ValueError("lineup snapshot report must be an object")
    players = [_snapshot_player(player) for player in payload["players"]]
    report = payload["report"]
    for field in ("current", "optimal", "start", "sit"):
        if not isinstance(report.get(field), list):
            raise ValueError(f"lineup snapshot report.{field} must be a list")
    return {**payload, "players": players}


def snapshot_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def retro_report(advice: dict[str, Any], actuals: WeeklyActualsBundle | dict[str, Any]) -> dict:
    """Compare snapshotted sit/start advice to locked weekly actuals."""
    snapshot = parse_lineup_snapshot(advice) if "kind" in advice else advice
    bundle = actuals if isinstance(actuals, WeeklyActualsBundle) else actuals
    if isinstance(bundle, WeeklyActualsBundle):
        data = bundle.data
        players = bundle.players
        matchups = bundle.matchups
        league = bundle.league
    else:
        data = bundle
        players = data["players"]
        matchups = data["matchups"]
        league = data["league"]

    by_id = {str(row["yahoo_player_id"]): row for row in players}
    team_key = snapshot["team_key"]
    if team_key not in _scoreboard_team_keys(matchups):
        raise ValueError(f"actuals scoreboard does not include snapshot team_key {team_key}")
    user_actuals = [row for row in players if row["team_key"] == team_key]
    recommended = snapshot["report"]["optimal"]
    advice_lineup = snapshot["report"]["current"]
    started = [row for row in user_actuals if is_starter(row.get("selected_position"))]

    recommended_total = _sum_actuals(recommended, by_id)
    started_total = _sum_actuals(started, by_id)
    advice_lineup_total = _sum_actuals(advice_lineup, by_id)
    started_ids = {_identity(row) for row in started}

    start_hits = [
        _scored(row, by_id) for row in snapshot["report"]["start"] if _identity(row) in started_ids
    ]
    start_misses = [
        _scored(row, by_id)
        for row in snapshot["report"]["start"]
        if _identity(row) not in started_ids
    ]
    sit_hits = [
        _scored(row, by_id)
        for row in snapshot["report"]["sit"]
        if _identity(row) not in started_ids
    ]
    sit_misses = [
        _scored(row, by_id) for row in snapshot["report"]["sit"] if _identity(row) in started_ids
    ]

    needed = recommended + started + snapshot["report"]["start"] + snapshot["report"]["sit"]
    missing = []
    seen: set[str] = set()
    for row in needed:
        ident = _identity(row)
        if ident in seen or ident in by_id:
            continue
        seen.add(ident)
        missing.append({"yahoo_player_id": ident, "name": row.get("name") or ""})

    return {
        "season": snapshot["season"],
        "week": snapshot["week"],
        "team_name": snapshot["team_name"],
        "team_key": team_key,
        "generated_at": snapshot["generated_at"],
        "synced_at": data["synced_at"],
        "recommended_total": recommended_total,
        "started_total": started_total,
        "advice_lineup_total": advice_lineup_total,
        "delta": round(recommended_total - started_total, 2),
        "start_hits": start_hits,
        "start_misses": start_misses,
        "sit_hits": sit_hits,
        "sit_misses": sit_misses,
        "missing_actuals": missing,
        "source_accuracy": _source_accuracy(snapshot["players"], by_id),
        "matchup": _user_matchup(matchups, team_key),
        "league_week": league["week"],
    }


def _snapshot_player(player: dict[str, Any]) -> dict[str, Any]:
    row = {
        "yahoo_player_id": str(player.get("yahoo_player_id") or ""),
        "yahoo_player_key": player.get("yahoo_player_key") or "",
        "name": player.get("name") or player.get("full_name") or "",
        "position": player.get("position"),
        "team": player.get("team"),
        "selected_position": player.get("selected_position"),
        "projection_key": player.get("projection_key"),
        "player_key": player.get("player_key"),
        "points": player.get("points"),
        "source_points": dict(player.get("source_points") or {}),
        "n": player.get("n", 0),
        "matched": bool(player.get("matched")),
    }
    if set(row) != _SNAPSHOT_PLAYER_KEYS:
        raise ValueError("lineup snapshot player has unknown or missing fields")
    return row


def _identity_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "yahoo_player_id": str(row.get("yahoo_player_id") or ""),
        "name": row.get("name") or "",
        "slot": row.get("slot") or row.get("selected_position"),
        "selected_position": row.get("selected_position"),
        "points": row.get("points"),
    }


def _identity(row: dict[str, Any]) -> str:
    return str(row.get("yahoo_player_id") or "")


def _sum_actuals(rows: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> float:
    total = 0.0
    for row in rows:
        actual = by_id.get(_identity(row))
        if actual is None:
            continue
        total += float(actual["points"])
    return round(total, 2)


def _scored(row: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    actual = by_id.get(_identity(row))
    return {
        "yahoo_player_id": _identity(row),
        "name": row.get("name") or (actual or {}).get("name") or "",
        "slot": row.get("slot") or row.get("selected_position"),
        "projected": row.get("points"),
        "actual": None if actual is None else actual["points"],
        "selected_position": None if actual is None else actual.get("selected_position"),
    }


def _source_accuracy(
    players: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    errors: dict[str, list[float]] = {}
    for player in players:
        actual = by_id.get(str(player.get("yahoo_player_id") or ""))
        if actual is None:
            continue
        actual_points = float(actual["points"])
        for source, projected in (player.get("source_points") or {}).items():
            if projected is None:
                continue
            errors.setdefault(source, []).append(float(projected) - actual_points)
    rows = []
    for source, deltas in sorted(errors.items()):
        abs_err = [abs(delta) for delta in deltas]
        rows.append(
            {
                "source": source,
                "n": len(deltas),
                "mae": round(sum(abs_err) / len(abs_err), 2),
                "bias": round(sum(deltas) / len(deltas), 2),
            }
        )
    return rows


def _scoreboard_team_keys(matchups: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for matchup in matchups:
        for team in matchup.get("teams") or []:
            key = team.get("team_key")
            if key:
                keys.add(key)
    return keys


def _user_matchup(matchups: list[dict[str, Any]], team_key: str) -> dict[str, Any] | None:
    for matchup in matchups:
        teams = matchup.get("teams") or []
        keys = [team["team_key"] for team in teams]
        if team_key not in keys:
            continue
        user = next(team for team in teams if team["team_key"] == team_key)
        opponent = next(team for team in teams if team["team_key"] != team_key)
        return {
            "matchup_id": matchup.get("matchup_id"),
            "user_team_key": team_key,
            "user_points": user["points"],
            "opponent_team_key": opponent["team_key"],
            "opponent_points": opponent["points"],
        }
    return None
