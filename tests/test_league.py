"""LeagueBundle contract and stored-state behavior."""

import copy
import json
from pathlib import Path

import pytest

from ffb.league import parse_bundle

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_league_minimal.json"


def _bundle(**changes):
    data = json.loads(FIXTURE.read_text())
    data.update(changes)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"extra": "typo"}, "unknown"),
        ({"rosters": []}, "every team"),
    ],
)
def test_bundle_rejects_closed_schema_and_missing_current_week_coverage(change, message):
    with pytest.raises(ValueError, match=message):
        parse_bundle(_bundle(**change), season=2024)


def test_bundle_rejects_roster_for_a_different_week():
    data = _bundle()
    data["rosters"][0]["week"] = 2
    with pytest.raises(ValueError, match="current_week"):
        parse_bundle(data, season=2024)


def test_replace_league_state_keeps_earlier_rosters_and_resolves_yahoo_ids(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    data = _bundle()
    data["rosters"][0]["players"] = [
        {
            "yahoo_player_id": "29279",
            "yahoo_player_key": "1.p.29279",
            "name": "Derrick Henry",
            "nfl_team": "BAL",
            "primary_position": "RB",
            "eligible_positions": ["RB"],
            "selected_position": "RB",
        },
        {
            "yahoo_player_id": "missing",
            "yahoo_player_key": "1.p.missing",
            "name": "Unknown",
            "nfl_team": None,
            "primary_position": "WR",
            "eligible_positions": ["WR"],
            "selected_position": "BN",
        },
    ]
    assert store.replace_league_state(parse_bundle(data, season=2024)) == {
        "teams": 1,
        "players": 2,
        "matched": 1,
        "unmatched": 1,
    }
    rows = store.league_roster_rows(2024)
    assert {(row["player_key"], row["matched"]) for row in rows} == {
        ("12626", True),
        ("yahoo:missing", False),
    }

    store.conn.execute(
        "INSERT INTO league_rosters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            2024,
            0,
            "1.l.mock-1.t.1",
            "old",
            "1.p.old",
            "Old",
            None,
            "QB",
            "[]",
            "QB",
            "yahoo:old",
            False,
        ],
    )
    changed = copy.deepcopy(data)
    changed["rosters"][0]["players"] = []
    store.replace_league_state(parse_bundle(changed, season=2024))
    assert store.league_roster_rows(2024) == []
    assert len(store.league_roster_rows(2024, week=0)) == 1
