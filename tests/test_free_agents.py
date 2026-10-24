"""FreeAgentsBundle v1 is closed: extra keys, empty pools, and bad ids fail."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ffb.free_agents import parse_free_agents, pool_age

FIXTURE = json.loads(Path("tests/fixtures/free_agents_minimal.json").read_text())


def _bundle(change=None):
    data = copy.deepcopy(FIXTURE)
    if change:
        change(data)
    return data


def test_accepts_the_committed_minimal_fixture():
    bundle = parse_free_agents(FIXTURE, season=2024)
    assert bundle.league["week"] == 2
    assert [player["name"] for player in bundle.players] == ["Waiver Back"]


def test_accepts_a_null_team_and_yahoo_source():
    def change(data):
        data["source"] = "yahoo"
        data["players"][0]["nfl_team"] = None

    assert parse_free_agents(_bundle(change), season=2024).players[0]["nfl_team"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(extra=1), "bundle has unknown or missing fields"),
        (lambda d: d.update(schema_version=2), "schema_version must be 1"),
        (lambda d: d.update(source="sleeper"), "source must be fixture or yahoo"),
        (lambda d: d.update(synced_at="2024-09-12 14:00:00Z"), "RFC 3339 UTC"),
        (lambda d: d.update(synced_at="2024-09-12T14:00:00-00:00"), "RFC 3339 UTC"),
        (lambda d: d["league"].update(extra=1), "league has unknown or missing fields"),
        (lambda d: d.update(players=[]), "empty pool is a failed scrape"),
        (lambda d: d["players"][0].update(extra=1), r"players\[0\] has unknown or missing"),
        (lambda d: d["players"][0].update(eligible_positions="RB"), "must be a list"),
        (lambda d: d["players"][0].update(eligible_positions=[""]), "nonempty string"),
        (lambda d: d["players"][0].update(nfl_team=""), "nonempty string"),
        (lambda d: d["players"].append(copy.deepcopy(d["players"][0])), "must be unique"),
    ],
)
def test_rejects_closed_schema_violations(change, message):
    with pytest.raises(ValueError, match=message):
        parse_free_agents(_bundle(change), season=2024)


def test_rejects_season_mismatch():
    with pytest.raises(ValueError, match="does not match bundle season"):
        parse_free_agents(FIXTURE, season=2026)


def test_rejects_a_pool_from_another_league():
    assert parse_free_agents(FIXTURE, season=2024, league_key="yahoo:423.l.1").league
    with pytest.raises(ValueError, match="bundle league yahoo:423.l.1 does not match"):
        parse_free_agents(FIXTURE, season=2024, league_key="yahoo:other-league")


def test_pool_age_is_measured_from_the_scrape():
    bundle = parse_free_agents(FIXTURE, season=2024)
    now = datetime(2024, 9, 17, 14, 0, tzinfo=UTC)
    assert pool_age(bundle, now=now) == timedelta(days=5)
