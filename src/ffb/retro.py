"""Weekly retro: join sit/start advice snapshots to weekly actuals.

Pure compute. The CLI owns snapshot I/O; this module never touches DuckDB,
KV, or the filesystem. Identity is ``native_id`` only — never name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ffb import config
from ffb.actuals import WeeklyActualsBundle, parse_actuals
from ffb.lineup import BENCH_SLOT, NON_STARTING_SLOTS, _assign_optimal, compare_lineup, is_starter

_UNSTARTABLE_SLOTS = NON_STARTING_SLOTS - {BENCH_SLOT}

RAW_CONSENSUS_WARNING = "Calibration params missing; using raw consensus."

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
    "native_id",
    "native_player_key",
    "name",
    "position",
    "team",
    "selected_position",
    "eligible_positions",
    "projection_key",
    "player_key",
    "points",
    "source_points",
    "n",
    "matched",
}


def league_snapshot_segment(league_key: str | None) -> str:
    """Path segment naming a league, or "" for the one that owned these paths.

    The default league keeps its historic flat paths (``lineup/2026_week3``) so
    existing snapshots stay readable; any other league nests under its own
    segment. ``:`` becomes ``_`` because the key is used as a path.
    """
    if not league_key or league_key == config.YAHOO_LEAGUE_KEY:
        return ""
    return f"{league_key.replace(':', '_')}/"


def lineup_snapshot_key(season: int, week: int, league_key: str | None = None) -> str:
    return f"lineup/{league_snapshot_segment(league_key)}{season}_week{week}"


def snapshot_stamp(generated_at: str) -> str:
    """A ``generated_at`` timestamp usable as a snapshot path segment."""
    return generated_at.replace(":", "-")


def lineup_history_key(
    season: int, week: int, generated_at: str, league_key: str | None = None
) -> str:
    """One immutable copy per written sit/start snapshot; the flat key holds the latest."""
    segment = league_snapshot_segment(league_key)
    return f"lineup/history/{segment}{season}_week{week}/{snapshot_stamp(generated_at)}"


def actuals_snapshot_key(season: int, week: int, league_key: str | None = None) -> str:
    return f"actuals/{league_snapshot_segment(league_key)}{season}_week{week}"


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
    bundle = (
        actuals
        if isinstance(actuals, WeeklyActualsBundle)
        else parse_actuals(actuals, season=snapshot["season"])
    )
    data = bundle.data
    players = bundle.players
    matchups = bundle.matchups
    league = bundle.league

    by_id = {str(row["native_id"]): row for row in players}
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

    snapshot_by_id = {_identity(row): row for row in snapshot["players"]}
    pool = _hindsight_pool(snapshot["players"], by_id, team_key)
    pool_ids = {_identity(row) for row in pool}
    hindsight_optimal = _assign_optimal(
        pool,
        snapshot["roster_slots"],
        use_display_points=False,
    )
    hindsight_total = _sum_actuals(hindsight_optimal, by_id)
    started_on_snapshot = [row for row in started if _identity(row) in pool_ids]
    hindsight_started_total = _sum_actuals(started_on_snapshot, by_id)
    hindsight_ids = {_identity(row) for row in hindsight_optimal}
    hindsight_start = [
        _hindsight_scored(row, by_id, snapshot_by_id)
        for row in hindsight_optimal
        if _identity(row) not in started_ids
    ]
    hindsight_sit = [
        _hindsight_scored(row, by_id, snapshot_by_id)
        for row in started_on_snapshot
        if _identity(row) not in hindsight_ids
    ]

    needed = recommended + started + snapshot["report"]["start"] + snapshot["report"]["sit"] + pool
    missing = []
    seen: set[str] = set()
    for row in needed:
        ident = _identity(row)
        if ident in seen or ident in by_id:
            continue
        seen.add(ident)
        missing.append({"native_id": ident, "name": row.get("name") or ""})

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
        "hindsight_total": hindsight_total,
        "hindsight_started_total": hindsight_started_total,
        "hindsight_delta": round(hindsight_total - hindsight_started_total, 2),
        "hindsight_start": hindsight_start,
        "hindsight_sit": hindsight_sit,
        "missing_actuals": missing,
        "source_accuracy": _source_accuracy(snapshot["players"], by_id),
        "matchup": _user_matchup(matchups, team_key),
        "league_week": league["week"],
    }


def shadow_report(
    advice: dict[str, Any],
    actuals: WeeklyActualsBundle | dict[str, Any],
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What calibrated means would have started, scored on the same actuals.

    The published retro report stays the raw advice. This is a sibling section:
    it replays ``compare_lineup`` on the ledger's calibrated means when the run
    recorded a params file, and on consensus when it did not. With no ledger,
    the section reports the advice snapshot's own starters and says so.
    Close calls keep the 1.5-point rule and add win-probability calls when the
    ledger stored a calibrated sd. Neither list is part of the published report.
    """
    snapshot = parse_lineup_snapshot(advice) if "kind" in advice else advice
    bundle = (
        actuals
        if isinstance(actuals, WeeklyActualsBundle)
        else parse_actuals(actuals, season=snapshot["season"])
    )
    by_id = {str(row["native_id"]): row for row in bundle.players}
    team_key = snapshot["team_key"]
    if team_key not in _scoreboard_team_keys(bundle.matchups):
        raise ValueError(f"actuals scoreboard does not include snapshot team_key {team_key}")
    started = [
        row
        for row in bundle.players
        if row["team_key"] == team_key and is_starter(row.get("selected_position"))
    ]
    started_total = _sum_actuals(started, by_id)
    raw_optimal = snapshot["report"]["optimal"]
    raw_ids = {_identity(row) for row in raw_optimal}
    calibration = _ledger_calibration(ledger)
    if ledger is None:
        recommended_total = _sum_actuals(raw_optimal, by_id)
        return _shadow_body(
            model="raw",
            warning=RAW_CONSENSUS_WARNING,
            params_key=None,
            digest=None,
            recommended_total=recommended_total,
            started_total=started_total,
            matches_advice=True,
            start=[],
            sit=[],
            close_calls=[],
            probability_close_calls=[],
        )

    model = "calibrated" if calibration is not None else "raw"
    warning = None if calibration is not None else RAW_CONSENSUS_WARNING
    players = _shadow_players(ledger, model)
    replay = compare_lineup(players, snapshot["roster_slots"])
    recommended_total = _sum_actuals(replay["optimal"], by_id)
    shadow_ids = {_identity(row) for row in replay["optimal"]}
    start = [_scored(row, by_id) for row in replay["optimal"] if _identity(row) not in raw_ids]
    sit = [_scored(row, by_id) for row in raw_optimal if _identity(row) not in shadow_ids]
    # Imported here so calibration can keep using league path helpers from retro.
    from ffb.calibration import probability_close_calls

    return _shadow_body(
        model=model,
        warning=warning,
        params_key=None if calibration is None else calibration["params_key"],
        digest=None if calibration is None else calibration["sha256"],
        recommended_total=recommended_total,
        started_total=started_total,
        matches_advice=shadow_ids == raw_ids,
        start=start,
        sit=sit,
        close_calls=replay["close_calls"],
        probability_close_calls=probability_close_calls(players, replay["optimal"]),
    )


def _ledger_calibration(ledger: dict[str, Any] | None) -> dict[str, str] | None:
    if ledger is None or ledger.get("schema_version") == 1:
        return None
    calibration = ledger.get("calibration")
    if not isinstance(calibration, dict):
        return None
    return calibration


def _shadow_players(ledger: dict[str, Any], model: str) -> list[dict[str, Any]]:
    players = []
    for row in ledger.get("players") or []:
        if not row.get("is_user_team"):
            continue
        if model == "calibrated" and row.get("calibrated_mean") is not None:
            points = row["calibrated_mean"]
        else:
            points = row.get("consensus")
        status = row.get("injury_status")
        sd = row.get("calibrated_sd") if model == "calibrated" else None
        players.append(
            {
                "native_id": row.get("native_id"),
                "name": row.get("name") or "",
                "position": row.get("position"),
                "team": row.get("nfl_team"),
                "selected_position": row.get("selected_position"),
                "eligible_positions": list(row.get("eligible_positions") or []),
                "projection_key": row.get("projection_key"),
                "player_key": row.get("player_key"),
                "points": points,
                "sd": sd,
                "n": row.get("n") or 0,
                "matched": bool(row.get("matched")),
                "injury": None if not status else {"status": status},
            }
        )
    return players


def _shadow_body(
    *,
    model: str,
    warning: str | None,
    params_key: str | None,
    digest: str | None,
    recommended_total: float,
    started_total: float,
    matches_advice: bool,
    start: list[dict[str, Any]],
    sit: list[dict[str, Any]],
    close_calls: list[dict[str, Any]],
    probability_close_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "model": model,
        "warning": warning,
        "params_key": params_key,
        "sha256": digest,
        "recommended_total": recommended_total,
        "started_total": started_total,
        "delta": round(recommended_total - started_total, 2),
        "matches_advice": matches_advice,
        "start": start,
        "sit": sit,
        "close_calls": close_calls,
        "probability_close_calls": probability_close_calls,
    }


def _snapshot_player(player: dict[str, Any]) -> dict[str, Any]:
    row = {
        "native_id": str(player.get("native_id") or ""),
        "native_player_key": player.get("native_player_key") or "",
        "name": player.get("name") or player.get("full_name") or "",
        "position": player.get("position"),
        "team": player.get("team"),
        "selected_position": player.get("selected_position"),
        "eligible_positions": list(player.get("eligible_positions") or []),
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
        "native_id": str(row.get("native_id") or ""),
        "name": row.get("name") or "",
        "slot": row.get("slot") or row.get("selected_position"),
        "selected_position": row.get("selected_position"),
        "points": row.get("points"),
    }


def _identity(row: dict[str, Any]) -> str:
    return str(row.get("native_id") or "")


def _hindsight_pool(
    players: list[dict[str, Any]], by_id: dict[str, dict[str, Any]], team_key: str
) -> list[dict[str, Any]]:
    """Snapshot roster with actual points.

    Membership follows the actuals: players on IR/IL at game time, players
    dropped to another team, and waiver adds stay out. A snapshot IR/IL player
    with no actuals row is stale and stays out too.
    """
    pool: list[dict[str, Any]] = []
    for player in players:
        ident = _identity(player)
        actual = by_id.get(ident)
        if actual is None:
            if player.get("selected_position") in _UNSTARTABLE_SLOTS:
                continue
        elif actual.get("team_key") != team_key:
            continue
        elif actual.get("selected_position") in _UNSTARTABLE_SLOTS:
            continue
        row = dict(player)
        row["points"] = None if actual is None else actual["points"]
        if actual is not None:
            row["selected_position"] = actual.get("selected_position") or player.get(
                "selected_position"
            )
        pool.append(row)
    return pool


def _hindsight_scored(
    row: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    snapshot_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    snap = snapshot_by_id.get(_identity(row)) or row
    return _scored({**snap, "slot": row.get("slot") or row.get("selected_position")}, by_id)


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
        "native_id": _identity(row),
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
        actual = by_id.get(str(player.get("native_id") or ""))
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
