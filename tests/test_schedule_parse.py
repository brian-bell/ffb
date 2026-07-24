"""Pure parse tests for the nflverse season-schedule source (team bye weeks)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ffb.sources import schedule

FIXTURE = Path(__file__).parent / "fixtures" / "schedule_sample.json"


@pytest.fixture()
def raw() -> list:
    return json.loads(FIXTURE.read_text())


def _byes(rows: list[dict]) -> dict[str, int]:
    return {row["team"]: row["bye"] for row in rows}


def test_parse_derives_one_bye_per_team(raw):
    rows = schedule.parse_byes(raw, 2026)
    assert _byes(rows) == {
        "KCC": 2,
        "SFO": 2,
        "LAR": 3,
        "PHI": 3,
        "DEN": 4,
        "MIA": 4,
    }
    for row in rows:
        assert row["season"] == 2026
        assert row["source"] == "schedule"


def test_parse_ignores_non_regular_season_games(raw):
    # The fixture's POST game covers KCC/SFO's missing week 2; counting it
    # would leave both without a bye.
    byes = _byes(schedule.parse_byes(raw, 2026))
    assert byes["KCC"] == 2
    assert byes["SFO"] == 2


def test_parse_skips_ambiguous_teams(raw):
    # BUF/CHI only appear in weeks 1 and 4 — two candidate byes, so no guess.
    byes = _byes(schedule.parse_byes(raw, 2026))
    assert "BUF" not in byes
    assert "CHI" not in byes


def test_parse_skips_unknown_team_codes(raw):
    byes = _byes(schedule.parse_byes(raw, 2026))
    assert not any(team not in {"KCC", "SFO", "LAR", "PHI", "DEN", "MIA"} for team in byes)


def test_parse_never_raises_on_malformed_rows(raw):
    # Fixture carries a game without a week and a non-dict entry; both are
    # skipped without affecting the derived byes.
    assert len(schedule.parse_byes(raw, 2026)) == 6


def test_parse_empty_or_wrong_shape_returns_empty():
    assert schedule.parse_byes([], 2026) == []
    assert schedule.parse_byes({"status": "error"}, 2026) == []
    assert schedule.parse_byes(None, 2026) == []


def test_snapshot_key():
    assert schedule.snapshot_key(2026) == "nflverse/schedule_2026"
