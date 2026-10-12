"""Prediction ledger: league-wide weekly projections as they stood at one run.

Pure compute. ``ffb lineup`` writes one ledger per run (never replaced) so a
later retro can join what the model believed, for every rostered player in the
league, to the weekly actuals. Identity is ``native_id``, the actuals join key.
"""

from __future__ import annotations

from typing import Any

from ffb.retro import league_snapshot_segment, snapshot_stamp

PREDICTION_LEDGER_KIND = "prediction_ledger"
LEDGER_KEYS = {
    "schema_version",
    "kind",
    "season",
    "week",
    "league_key",
    "generated_at",
    "scoring_weights",
    "projection_sources",
    "players",
}
LEDGER_KEYS_V2 = LEDGER_KEYS | {"calibration"}
LEDGER_PLAYER_KEYS = {
    "team_key",
    "is_user_team",
    "native_id",
    "native_player_key",
    "player_key",
    "projection_key",
    "matched",
    "name",
    "position",
    "nfl_team",
    "selected_position",
    "eligible_positions",
    "injury_status",
    "consensus",
    "n",
    "source_points",
}
LEDGER_PLAYER_KEYS_V2 = LEDGER_PLAYER_KEYS | {"calibrated_mean", "calibrated_sd"}


def prediction_ledger_key(
    season: int, week: int, generated_at: str, league_key: str | None = None
) -> str:
    segment = league_snapshot_segment(league_key)
    return f"predlog/{segment}{season}_week{week}/{snapshot_stamp(generated_at)}"


def build_prediction_ledger(
    *,
    season: int,
    week: int,
    league_key: str,
    user_team_key: str,
    generated_at: str,
    scoring_weights: dict[str, float],
    projection_sources: dict[str, dict[str, Any]],
    players: list[dict[str, Any]],
    calibration: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Serialize roster rows carrying weekly points and injuries for every team.

    New runs are schema v2: a calibration reference plus per-player calibrated
    mean and sd. Those fields are null when the run fell back to raw consensus.
    """
    return parse_prediction_ledger(
        {
            "schema_version": 2,
            "kind": PREDICTION_LEDGER_KIND,
            "season": season,
            "week": week,
            "league_key": league_key,
            "generated_at": generated_at,
            "scoring_weights": dict(scoring_weights),
            "projection_sources": {
                source: dict(meta) for source, meta in projection_sources.items()
            },
            "calibration": calibration,
            "players": [_ledger_player(row, user_team_key) for row in players],
        }
    )


def parse_prediction_ledger(payload: object) -> dict[str, Any]:
    """Validate the closed ledger shape before it is written or joined.

    Schema v1 files written before calibration stay valid and are not upgraded
    in place. Schema v2 adds ``calibration`` and per-player calibrated mean/sd.
    """
    if not isinstance(payload, dict):
        raise ValueError("prediction ledger must be an object")
    version = payload.get("schema_version")
    if version == 1:
        keys, player_keys = LEDGER_KEYS, LEDGER_PLAYER_KEYS
    elif version == 2:
        keys, player_keys = LEDGER_KEYS_V2, LEDGER_PLAYER_KEYS_V2
    else:
        raise ValueError("prediction ledger schema_version must be 1 or 2")
    if set(payload) != keys:
        raise ValueError("prediction ledger has unknown or missing fields")
    if payload["kind"] != PREDICTION_LEDGER_KIND:
        raise ValueError("prediction ledger kind must be prediction_ledger")
    for key in ("season", "week"):
        if type(payload[key]) is not int or payload[key] <= 0:
            raise ValueError(f"prediction ledger {key} must be a positive integer")
    for key in ("league_key", "generated_at"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValueError(f"prediction ledger {key} must be a nonempty string")
    for key in ("scoring_weights", "projection_sources"):
        if not isinstance(payload[key], dict):
            raise ValueError(f"prediction ledger {key} must be an object")
    if version == 2:
        _validate_calibration_ref(payload["calibration"])
    if not isinstance(payload["players"], list):
        raise ValueError("prediction ledger players must be a list")
    for player in payload["players"]:
        if not isinstance(player, dict) or set(player) != player_keys:
            raise ValueError("prediction ledger player has unknown or missing fields")
        if version == 2:
            _validate_optional_number(player["calibrated_mean"], "calibrated_mean")
            _validate_optional_number(player["calibrated_sd"], "calibrated_sd")
    return payload


def _validate_calibration_ref(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"params_key", "sha256"}:
        raise ValueError("prediction ledger calibration must be null or params_key/sha256")
    for key in ("params_key", "sha256"):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f"prediction ledger calibration.{key} must be a nonempty string")


def _validate_optional_number(value: object, name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"prediction ledger {name} must be a number or null")


def _ledger_player(row: dict[str, Any], user_team_key: str) -> dict[str, Any]:
    injury = row.get("injury")
    return {
        "team_key": row.get("team_key"),
        "is_user_team": row.get("team_key") == user_team_key,
        "native_id": str(row.get("native_id") or ""),
        "native_player_key": row.get("native_player_key") or "",
        "player_key": row.get("player_key"),
        "projection_key": row.get("projection_key"),
        "matched": bool(row.get("matched")),
        "name": row.get("name") or "",
        "position": row.get("position"),
        "nfl_team": row.get("team"),
        "selected_position": row.get("selected_position"),
        "eligible_positions": list(row.get("eligible_positions") or []),
        "injury_status": injury.get("status") if isinstance(injury, dict) else None,
        "consensus": row.get("points"),
        "n": row.get("n", 0),
        "source_points": dict(row.get("source_points") or {}),
        "calibrated_mean": row.get("calibrated_mean"),
        "calibrated_sd": row.get("calibrated_sd"),
    }
