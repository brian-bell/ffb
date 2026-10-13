"""Prediction ledger: a point-in-time record of league-wide weekly projections."""

import pytest

from ffb.predlog import (
    PREDICTION_LEDGER_KIND,
    build_prediction_ledger,
    parse_prediction_ledger,
    prediction_ledger_key,
)

GENERATED_AT = "2026-10-10T11:02:14.123456Z"


def _player(**overrides):
    row = {
        "team_key": "t.1",
        "native_id": "4046",
        "native_player_key": "1.p.4046",
        "player_key": "mfl:1",
        "projection_key": "mfl:1",
        "matched": True,
        "name": "Some Back",
        "position": "RB",
        "team": "KC",
        "selected_position": "RB",
        "eligible_positions": ["RB", "W/R/T"],
        "points": 13.5,
        "n": 2,
        "source_points": {"sleeper": 14.2, "espn": 12.8},
        "injury": {"status": "QUESTIONABLE", "fetched_at": "2026-10-10T10:00:00Z"},
        "full_name": "Some Back",  # extra roster columns are dropped
    }
    row.update(overrides)
    return row


def _ledger(players):
    return build_prediction_ledger(
        season=2026,
        week=6,
        league_key="sleeper:123",
        user_team_key="t.1",
        generated_at=GENERATED_AT,
        scoring_weights={"rec": 1.0},
        projection_sources={"sleeper": {"key": "k", "sha256": "abc", "modified_at": "m"}},
        players=players,
    )


def test_ledger_records_every_rostered_player_with_its_projection():
    ledger = _ledger([_player(), _player(team_key="t.2", native_id="99", points=None, n=0)])

    assert ledger["kind"] == PREDICTION_LEDGER_KIND
    assert ledger["schema_version"] == 2
    assert ledger["calibration"] is None
    mine, theirs = ledger["players"]
    assert mine == {
        "team_key": "t.1",
        "is_user_team": True,
        "native_id": "4046",
        "native_player_key": "1.p.4046",
        "player_key": "mfl:1",
        "projection_key": "mfl:1",
        "matched": True,
        "name": "Some Back",
        "position": "RB",
        "nfl_team": "KC",
        "selected_position": "RB",
        "eligible_positions": ["RB", "W/R/T"],
        "injury_status": "QUESTIONABLE",
        "consensus": 13.5,
        "n": 2,
        "source_points": {"sleeper": 14.2, "espn": 12.8},
        "calibrated_mean": None,
        "calibrated_sd": None,
    }
    assert theirs["is_user_team"] is False
    assert theirs["consensus"] is None
    assert theirs["injury_status"] == "QUESTIONABLE"


def test_ledger_round_trips_through_its_parser():
    ledger = _ledger([_player(injury=None)])

    assert parse_prediction_ledger(ledger) == ledger
    assert ledger["players"][0]["injury_status"] is None


def test_parser_rejects_unknown_fields():
    ledger = _ledger([_player()])

    with pytest.raises(ValueError):
        parse_prediction_ledger({**ledger, "extra": 1})
    with pytest.raises(ValueError):
        parse_prediction_ledger({**ledger, "players": [{**ledger["players"][0], "x": 1}]})


def test_v1_ledger_still_parses_without_calibrated_fields():
    ledger = _ledger([_player()])
    v1 = {key: value for key, value in ledger.items() if key != "calibration"}
    v1["schema_version"] = 1
    v1["players"] = [
        {
            key: value
            for key, value in player.items()
            if key not in {"calibrated_mean", "calibrated_sd"}
        }
        for player in ledger["players"]
    ]

    parsed = parse_prediction_ledger(v1)

    assert parsed["schema_version"] == 1
    assert "calibration" not in parsed
    assert "calibrated_mean" not in parsed["players"][0]


def test_ledger_key_is_one_file_per_run_under_the_league_week():
    assert prediction_ledger_key(2026, 6, GENERATED_AT, "sleeper:123") == (
        "predlog/sleeper_123/2026_week6/2026-10-10T11-02-14.123456Z"
    )
