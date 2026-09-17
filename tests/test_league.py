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


def test_refresh_league_roster_identities_heals_yahoo_fallback_after_late_crosswalk(
    store, crosswalk_rows
):
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


# Accept/reject corpus for `bundle.synced_at`. The tracker's TypeScript port in
# `tracker/src/league-bundle.ts` carries the identical corpus; the two must stay
# in lockstep or the Worker and the CLI disagree about what a valid bundle is.
SYNCED_AT_ACCEPTED = (
    "2026-07-22T12:00:00Z",
    "2026-07-22T12:00:00+00:00",
    "2026-07-22T12:00:00-00:00",
    "2026-07-22 12:00:00Z",
    "2026-07-22T12:00:00.123456Z",
    "2026-07-22T12:00:00.123456789Z",
    "2026-07-22T12:00:00.1Z",
    "2026-07-22T12:00:00,123Z",
    "2026-07-22T12:00:00+0000",
    "2026-07-22T12:00:00-0000",
    "2026-07-22T12:00:00+00",
    "2026-07-22T12:00:00+00:00:00",
    "2026-07-22T12:00Z",
    "2026-07-22T12:00+00:00",
    "2024-02-29T00:00:00Z",
    "0001-01-01T00:00:00Z",
    "9999-12-31T23:59:59Z",
)

SYNCED_AT_REJECTED = (
    # Naive or non-UTC offsets.
    "2026-07-22T12:00:00",
    "2026-07-22 12:00:00",
    "2026-07-22T12:00:00+01:00",
    "2026-07-22T12:00:00-05:00",
    # Calendar-invalid: must fail rather than roll over.
    "2026-02-30T12:00:00Z",
    "2025-02-29T00:00:00Z",
    "2026-13-01T12:00:00Z",
    "2026-07-22T25:00:00Z",
    "2026-07-22T24:00:00Z",
    "2026-07-22T12:00:60Z",
    "0000-01-01T00:00:00Z",
    # ISO 8601 forms that are not RFC 3339; `fromisoformat` used to admit these.
    "2026-W30-3T12:00:00Z",
    "20260722T120000Z",
    "20260722T120000+0000",
    "2026-07-22T12Z",
    "2026-07-22",
    # Malformed.
    "2026-07-22t12:00:00z",
    "2026-07-22T12:00:00Z ",
    "+2026-07-22T12:00:00Z",
    "2026-7-22T12:00:00Z",
    "not-a-timestamp",
)


@pytest.mark.parametrize("text", SYNCED_AT_ACCEPTED)
def test_synced_at_accepts_rfc3339_utc(text):
    parse_bundle(_bundle(synced_at=text), season=2024)


@pytest.mark.parametrize("text", SYNCED_AT_REJECTED)
def test_synced_at_rejects_non_rfc3339_utc(text):
    with pytest.raises(ValueError, match="bundle.synced_at"):
        parse_bundle(_bundle(synced_at=text), season=2024)
