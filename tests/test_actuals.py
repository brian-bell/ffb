"""Closed WeeklyActualsBundle v1 contract."""

import copy
import json
from pathlib import Path

import pytest

from ffb.actuals import parse_actuals

FIXTURE = Path(__file__).parent / "fixtures" / "weekly_actuals_minimal.json"


def _bundle(**changes):
    data = json.loads(FIXTURE.read_text())
    data.update(changes)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"source": "grok"}, "source"),
        ({"extra": "typo"}, "unknown"),
        ({"matchups": []}, "matchups"),
    ],
)
def test_actuals_rejects_closed_schema_and_incomplete_scoreboard(change, message):
    with pytest.raises(ValueError, match=message):
        parse_actuals(_bundle(**change), season=2024)


def test_actuals_accepts_the_committed_minimal_fixture():
    bundle = parse_actuals(json.loads(FIXTURE.read_text()), season=2024)
    assert bundle.league["week"] == 1
    assert bundle.league["season"] == 2024
    assert len(bundle.matchups) == 1
    assert len(bundle.players) == 6


def test_actuals_rejects_season_mismatch():
    with pytest.raises(ValueError, match="season"):
        parse_actuals(_bundle(), season=2026)


def test_actuals_rejects_non_utc_timestamp():
    data = _bundle()
    data["synced_at"] = "2026-09-16T16:00:00-04:00"
    with pytest.raises(ValueError, match="UTC"):
        parse_actuals(data, season=2024)


def test_actuals_rejects_matchup_week_mismatch():
    data = _bundle()
    data["matchups"][0]["week"] = 2
    with pytest.raises(ValueError, match="week"):
        parse_actuals(data, season=2024)


def test_actuals_rejects_duplicate_player_ids():
    data = _bundle()
    data["players"].append(copy.deepcopy(data["players"][0]))
    with pytest.raises(ValueError, match="Yahoo player IDs"):
        parse_actuals(data, season=2024)


def test_actuals_rejects_player_team_outside_scoreboard():
    data = _bundle()
    data["players"][0]["team_key"] = "1.l.sit.t.9"
    with pytest.raises(ValueError, match="team_key"):
        parse_actuals(data, season=2024)


def test_actuals_rejects_odd_team_count():
    data = _bundle()
    data["league"]["num_teams"] = 3
    with pytest.raises(ValueError, match="even"):
        parse_actuals(data, season=2024)


def test_actuals_rejects_non_finite_points():
    data = _bundle()
    data["players"][0]["points"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        parse_actuals(data, season=2024)
