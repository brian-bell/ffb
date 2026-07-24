"""Pure parse tests for the nflverse season-schedule source (team bye weeks)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ffb import config
from ffb.sources import schedule

FIXTURE = Path(__file__).parent / "fixtures" / "schedule_sample.json"


@pytest.fixture()
def raw() -> list:
    return json.loads(FIXTURE.read_text())


def _byes(rows: list[dict]) -> dict[str, int]:
    return {row["team"]: row["bye"] for row in rows}


def _reg(week: int, home: str, away: str) -> dict:
    return {
        "season": 2026,
        "game_type": "REG",
        "week": week,
        "home_team": home,
        "away_team": away,
    }


def test_parse_derives_one_bye_per_team_for_the_full_league(raw):
    rows = schedule.parse_byes(raw, 2026)
    byes = _byes(rows)
    assert len(byes) == 32  # complete fixture: every canonical team has a bye
    # Teams whose schedule codes need aliasing (KC->KCC, SF->SFO, LA->LAR).
    assert byes["KCC"] == 2
    assert byes["SFO"] == 2
    assert byes["LAR"] == 3
    # Already-canonical codes.
    assert byes["PHI"] == 3
    assert byes["DEN"] == 4
    assert byes["MIA"] == 4
    assert byes["BAL"] == 4
    assert byes["CIN"] == 4
    for row in rows:
        assert row["season"] == 2026
        assert row["source"] == "schedule"


def test_parse_ignores_non_regular_season_games(raw):
    # The fixture's POST game covers KCC/SFO's missing week 2; counting it
    # would leave both without a bye.
    byes = _byes(schedule.parse_byes(raw, 2026))
    assert byes["KCC"] == 2
    assert byes["SFO"] == 2


def test_parse_skips_ambiguous_teams():
    # BUF only appears in weeks 1 and 4 — two candidate byes, so no guess.
    raw = [
        _reg(1, "BUF", "DEN"),
        _reg(1, "PHI", "DEN"),
        _reg(2, "CHI", "PHI"),
        _reg(2, "DEN", "CHI"),
        _reg(3, "CHI", "PHI"),
        _reg(4, "BUF", "CHI"),
        _reg(4, "DEN", "CHI"),
    ]
    byes = _byes(schedule.parse_byes(raw, 2026))
    assert "BUF" not in byes
    assert byes == {"CHI": 1, "PHI": 4, "DEN": 3}


def test_parse_skips_unknown_team_codes(raw):
    byes = _byes(schedule.parse_byes(raw, 2026))
    assert all(team in config.NFL_TEAM_CODES for team in byes)  # XYZ never stored


def test_parse_never_raises_on_malformed_rows(raw):
    # Fixture carries a game without a week and a non-dict entry; both are
    # skipped without affecting the derived byes.
    assert len(schedule.parse_byes(raw, 2026)) == 32


def test_parse_empty_or_wrong_shape_returns_empty():
    assert schedule.parse_byes([], 2026) == []
    assert schedule.parse_byes({"status": "error"}, 2026) == []
    assert schedule.parse_byes(None, 2026) == []


def test_snapshot_key():
    assert schedule.snapshot_key(2026) == "nflverse/schedule_2026"
