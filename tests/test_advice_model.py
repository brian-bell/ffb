"""Phase 4: choose raw vs calibrated advice, snapshot fields, and close-call rule."""

import pytest

from ffb.backtest import resolve_advice_model
from ffb.calibration import (
    apply_advice_points,
    decorate_advice_report,
    fit_calibration,
    start_probabilities,
)
from ffb.lineup import compare_lineup
from ffb.retro import RAW_CONSENSUS_WARNING


def _player(
    native_id, name, points, selected, *, sd=None, calibrated_mean=None, calibrated_sd=None
):
    row = {
        "native_id": native_id,
        "name": name,
        "position": "RB",
        "eligible_positions": ["RB"],
        "selected_position": selected,
        "points": points,
        "n": 1,
        "source_points": {"sleeper": points},
        "matched": True,
        "player_key": native_id,
        "projection_key": native_id,
        "team": "BAL",
    }
    if sd is not None:
        row["sd"] = sd
    if calibrated_mean is not None:
        row["calibrated_mean"] = calibrated_mean
    if calibrated_sd is not None:
        row["calibrated_sd"] = calibrated_sd
    return row


SLOTS = {"RB": 1, "BN": 2}


def test_default_model_follows_the_gate_and_falls_back_to_raw():
    assert resolve_advice_model(None, params={"sha256": "x"}, gate_passed=True) == (
        "calibrated",
        None,
    )
    assert resolve_advice_model(None, params={"sha256": "x"}, gate_passed=False) == ("raw", None)
    assert resolve_advice_model(None, params=None, gate_passed=True) == ("raw", None)


def test_explicit_calibrated_is_opt_in_and_falls_back_without_params():
    assert resolve_advice_model("calibrated", params={"sha256": "x"}, gate_passed=False) == (
        "calibrated",
        None,
    )
    assert resolve_advice_model("calibrated", params=None, gate_passed=True) == (
        "raw",
        RAW_CONSENSUS_WARNING,
    )


def test_explicit_raw_ignores_the_gate():
    assert resolve_advice_model("raw", params={"sha256": "x"}, gate_passed=True) == ("raw", None)


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="raw or calibrated"):
        resolve_advice_model("shadow", params=None, gate_passed=False)


def test_apply_advice_points_uses_calibrated_mean_only_when_asked():
    players = [
        _player("a", "Alpha", 10.0, "RB", calibrated_mean=12.0, calibrated_sd=3.0),
        _player("b", "Bravo", 8.0, "BN", calibrated_mean=None, calibrated_sd=None),
    ]
    raw = apply_advice_points(players, "raw")
    cal = apply_advice_points(players, "calibrated")

    assert [row["points"] for row in raw] == [10.0, 8.0]
    assert raw[0]["sd"] is None
    assert [row["points"] for row in cal] == [12.0, 8.0]
    assert cal[0]["sd"] == 3.0
    assert cal[1]["sd"] is None
    assert players[0]["points"] == 10.0


def test_empty_history_calibrated_advice_matches_raw_starters():
    players = [
        _player(
            "a",
            "Alpha",
            12.0,
            "BN",
            calibrated_mean=12.0,
            calibrated_sd=0.0,
            sd=0.0,
        ),
        _player(
            "b",
            "Bravo",
            10.0,
            "RB",
            calibrated_mean=10.0,
            calibrated_sd=0.0,
            sd=0.0,
        ),
    ]
    params = fit_calibration([], season=2026, week=1, league_key="yahoo:470.l.928421")
    annotated = [
        {**player, "calibrated_mean": player["points"], "calibrated_sd": 0.0} for player in players
    ]
    # Identity params store sd 0, and the mean equals consensus.
    assert params["fit_weeks"] == []
    raw = apply_advice_points(annotated, "raw")
    cal = apply_advice_points(annotated, "calibrated")
    raw_report = compare_lineup(raw, SLOTS)
    cal_report = compare_lineup(cal, SLOTS)
    assert [row["name"] for row in raw_report["optimal"]] == [
        row["name"] for row in cal_report["optimal"]
    ]
    assert [row["points"] for row in raw] == [row["points"] for row in cal]


def test_start_probabilities_are_null_without_sd_and_certain_with_zero_spread():
    players = [
        _player("a", "Alpha", 10.0, "RB", sd=0.0),
        _player("b", "Bravo", 8.0, "BN", sd=0.0),
        _player("c", "Charlie", 9.0, "BN"),
    ]
    report = compare_lineup(players, SLOTS)
    probs = start_probabilities(players, report["optimal"])
    assert probs["a"] == 1.0
    assert probs["b"] == 0.0
    assert probs["c"] is None


def test_start_probabilities_use_the_closest_eligible_pair():
    players = [
        _player("a", "Alpha", 10.0, "RB", sd=10.0),
        _player("b", "Bravo", 9.0, "BN", sd=10.0),
    ]
    report = compare_lineup(players, SLOTS)
    probs = start_probabilities(players, report["optimal"])
    assert probs["a"] == pytest.approx(0.5282, abs=1e-3)
    assert probs["b"] == pytest.approx(0.4718, abs=1e-3)
    assert probs["a"] + probs["b"] == pytest.approx(1.0)


def test_decorate_replaces_close_calls_only_when_calibrated():
    players = [
        _player("a", "Alpha", 10.0, "RB", sd=10.0, calibrated_mean=10.0, calibrated_sd=10.0),
        _player("b", "Bravo", 9.0, "BN", sd=10.0, calibrated_mean=9.0, calibrated_sd=10.0),
        _player("c", "Charlie", 7.0, "BN", sd=20.0, calibrated_mean=7.0, calibrated_sd=20.0),
    ]
    raw_players = apply_advice_points(players, "raw")
    cal_players = apply_advice_points(players, "calibrated")
    raw = decorate_advice_report(compare_lineup(raw_players, SLOTS), raw_players, "raw")
    cal = decorate_advice_report(compare_lineup(cal_players, SLOTS), cal_players, "calibrated")

    assert raw["model"] == "raw"
    assert [row["name"] for row in raw["close_calls"]] == ["Bravo"]
    assert "p_win" not in raw["close_calls"][0]
    assert raw["optimal"][0].get("p_start") is None

    assert cal["model"] == "calibrated"
    assert [row["name"] for row in cal["close_calls"]] == ["Bravo", "Charlie"]
    assert all(row["p_win"] < 0.6 for row in cal["close_calls"])
    assert cal["optimal"][0]["p_start"] == pytest.approx(0.5282, abs=1e-3)
    assert cal["optimal"][0]["sd"] == 10.0
