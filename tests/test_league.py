"""LeagueBundle contract and stored-state behavior."""

import copy
import json
from pathlib import Path

import pytest

from ffb import config
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
        "INSERT INTO league_rosters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            2024,
            "yahoo:1.l.mock-1",
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


def _stored(store, *, league_key, source, name, synced_at, season=2024):
    """Persist a minimal league so selection can be exercised across providers."""
    data = _bundle()
    data["source"] = source
    data["synced_at"] = synced_at
    data["league"] = data["league"] | {"league_key": league_key, "name": name}
    store.replace_league_state(parse_bundle(data, season=season))


def test_resolve_league_key_prefers_explicit_then_yahoo_then_most_recent_sync(store):
    """Callers that predate the --league selector must keep last-sync-wins behavior."""
    _stored(
        store,
        league_key="1.l.older",
        source="fixture",
        name="Older",
        synced_at="2026-09-01T00:00:00Z",
    )
    assert store.resolve_league_key(2024) == "yahoo:1.l.older"

    _stored(
        store,
        league_key="1.l.newer",
        source="fixture",
        name="Newer",
        synced_at="2026-09-10T00:00:00Z",
    )
    # Alphabetically first is "1.l.newer", but recency is what decides.
    assert store.league_keys(2024) == ["yahoo:1.l.newer", "yahoo:1.l.older"]
    assert store.resolve_league_key(2024) == "yahoo:1.l.newer"
    assert store.resolve_league_key(2024, "yahoo:1.l.older") == "yahoo:1.l.older"


def test_replace_league_state_scopes_to_one_league(store):
    """Syncing one league must not delete another league's state for the season."""
    _stored(
        store,
        league_key="470.l.928421",
        source="yahoo",
        name="Yahoo",
        synced_at="2026-09-01T00:00:00Z",
    )
    _stored(
        store,
        league_key="1395854363380965376",
        source="sleeper",
        name="Sleeper",
        synced_at="2026-09-10T00:00:00Z",
    )
    assert store.league_keys(2024) == [
        "sleeper:1395854363380965376",
        "yahoo:470.l.928421",
    ]
    # The configured Yahoo league stays the default even though Sleeper is newer.
    assert store.resolve_league_key(2024) == config.YAHOO_LEAGUE_KEY
    assert store.league_context(2024, config.YAHOO_LEAGUE_KEY)["name"] == "Yahoo"
    assert store.league_context(2024, config.SLEEPER_LEAGUE_KEY)["name"] == "Sleeper"

    # Re-syncing Yahoo replaces only Yahoo.
    _stored(
        store,
        league_key="470.l.928421",
        source="yahoo",
        name="Yahoo Again",
        synced_at="2026-09-12T00:00:00Z",
    )
    assert store.league_context(2024, config.YAHOO_LEAGUE_KEY)["name"] == "Yahoo Again"
    assert store.league_context(2024, config.SLEEPER_LEAGUE_KEY)["name"] == "Sleeper"


def test_backfilling_a_past_week_does_not_move_the_league_clock_backwards(store):
    """A backfill pins its bundle to a past week; the league's week must not follow."""
    data = _bundle()
    data["league"] = data["league"] | {"current_week": 5}
    for roster in data["rosters"]:
        roster["week"] = 5
    store.replace_league_state(parse_bundle(data, season=2024))
    assert store.league_context(2024)["current_week"] == 5

    past = _bundle()
    past["league"] = past["league"] | {"current_week": 2}
    for roster in past["rosters"]:
        roster["week"] = 2
    store.replace_league_state(parse_bundle(past, season=2024))
    assert store.league_context(2024)["current_week"] == 5


def test_stored_defenses_keep_their_canonical_key(store, crosswalk_rows):
    """A D/ST has its own identity; storing it unmatched mislabels a player that scores."""
    store.upsert_crosswalk(crosswalk_rows)
    data = _bundle()
    data["rosters"][0]["players"] = [
        {
            "native_id": "SF",
            "native_player_key": "sleeper:SF",
            "name": "49ers",
            "nfl_team": "SFO",
            "primary_position": "DEF",
            "eligible_positions": ["DEF"],
            "selected_position": "DEF",
        }
    ]
    store.replace_league_state(parse_bundle(data, season=2024))
    [row] = store.league_roster_rows(2024)
    assert row["player_key"] == "def:SFO"
    # matched matters: the reader warns that unmatched rows "score zero", which
    # would be false for a defense that lineup.projection_key resolves anyway.
    assert row["matched"] is True
    assert row["primary_position"] == "DEF"


def test_first_sync_backfill_keeps_the_live_week_as_current(store):
    """A backfill must not make a past week the league's current week."""
    data = _bundle()
    data["league"] = data["league"] | {"current_week": 2}
    for roster in data["rosters"]:
        roster["week"] = 2
    # The provider reported the live week alongside the backfilled one.
    data["settings"] = data["settings"] | {
        "provider_settings": {**data["settings"]["provider_settings"], "nfl_week": 7}
    }
    store.replace_league_state(parse_bundle(data, season=2024))
    assert store.league_context(2024)["current_week"] == 7
