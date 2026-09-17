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
        ({"schema_version": 3}, "schema_version"),
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
            "native_id": "29279",
            "native_player_key": "1.p.29279",
            "name": "Derrick Henry",
            "nfl_team": "BAL",
            "primary_position": "RB",
            "eligible_positions": ["RB"],
            "selected_position": "RB",
        },
        {
            "native_id": "missing",
            "native_player_key": "1.p.missing",
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


def test_refresh_league_roster_identities_heals_yahoo_fallback_after_late_crosswalk(
    store, crosswalk_rows
):
    data = _bundle()
    data["rosters"][0]["players"] = [
        {
            "native_id": "29279",
            "native_player_key": "1.p.29279",
            "name": "Derrick Henry",
            "nfl_team": "BAL",
            "primary_position": "RB",
            "eligible_positions": ["RB"],
            "selected_position": "RB",
        }
    ]
    store.replace_league_state(parse_bundle(data, season=2024))
    stuck = store.league_roster_rows(2024)[0]
    assert stuck["player_key"] == "yahoo:29279"
    assert stuck["matched"] is False

    store.upsert_crosswalk(crosswalk_rows)
    assert store.refresh_league_roster_identities(2024) == 1

    healed = store.league_roster_rows(2024)[0]
    assert healed["player_key"] == "12626"
    assert healed["matched"] is True
    assert healed["full_name"] == "Derrick Henry"
    assert store.refresh_league_roster_identities(2024) == 0


def _v1_bundle():
    """A schema-v1 bundle: one roster carries the pre-cutover identity spelling."""
    data = json.loads(
        (Path(__file__).parent / "fixtures" / "yahoo_lineup_sitstart.json").read_text()
    )
    data["schema_version"] = 1
    for roster in data["rosters"]:
        for player in roster["players"]:
            player["yahoo_player_id"] = player.pop("native_id")
            player["yahoo_player_key"] = player.pop("native_player_key")
    return data


def test_parse_bundle_upgrades_schema_v1_identity_fields():
    """v1 bundles at rest in KV still read, normalized to the v2 spelling."""
    bundle = parse_bundle(_v1_bundle(), season=2024)
    assert bundle.data["schema_version"] == 2
    players = [p for roster in bundle.rosters for p in roster["players"]]
    assert players
    for player in players:
        assert "yahoo_player_id" not in player
        assert player["native_id"] and player["native_player_key"]


def test_parse_bundle_rejects_a_bundle_mixing_v1_and_v2_identity_fields():
    data = _v1_bundle()
    data["rosters"][0]["players"][0]["native_id"] = "collide"
    with pytest.raises(ValueError, match="mixes schema-v1 and schema-v2"):
        parse_bundle(data, season=2024)
