"""Parsing raw ESPN player objects into normalized projection rows."""

import json
from pathlib import Path

import pytest

from ffb.scoring import ppr_points
from ffb.sources.espn import parse_projections

FIXTURE = Path(__file__).parent / "fixtures" / "espn_projections_sample.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text())


def test_parses_season_projection_and_translates_stats(raw):
    rows = parse_projections(raw, season=2024)
    henry = next(r for r in rows if r["native_id"] == "3043078")
    assert henry["full_name"] == "Derrick Henry"
    assert henry["position"] == "RB"
    assert henry["source"] == "espn"
    assert henry["scope"] == "season"
    assert henry["season"] == 2024
    # ESPN numeric stat ids translated to our keys.
    assert henry["stats"]["rush_yd"] == 1075.87
    assert henry["stats"]["rush_td"] == 11.58
    assert henry["stats"]["rec"] == 23.57
    assert henry["stats"]["fum_lost"] == 0.89


def test_unmapped_stat_ids_are_dropped(raw):
    henry = next(r for r in parse_projections(raw, season=2024) if r["native_id"] == "3043078")
    # id 58 (targets) is unmapped -> must not leak a numeric key into stats.
    assert "58" not in henry["stats"]
    assert all(isinstance(k, str) and not k.isdigit() for k in henry["stats"])


def test_translated_stats_score_sensibly(raw):
    # The whole point of translation: our PPR scores ESPN stats like Sleeper's.
    allen = next(r for r in parse_projections(raw, season=2024) if r["native_id"] == "3918298")
    assert 290 < ppr_points(allen["stats"]) < 330  # elite-QB range


def test_ignores_actuals_keeps_projection(raw):
    henry = next(r for r in parse_projections(raw, season=2024) if r["native_id"] == "3043078")
    # The statSourceId=0 (actual) line has rush_yd 999; projection has 1075.87.
    assert henry["stats"]["rush_yd"] == 1075.87


def test_skips_players_without_season_projection(raw):
    rows = parse_projections(raw, season=2024)
    assert all(r["native_id"] != "999901" for r in rows)  # actual-only player


def test_skips_malformed_projection_without_stats(raw):
    rows = parse_projections(raw, season=2024)
    assert all(r["native_id"] != "999902" for r in rows)  # projection entry has no stats


def test_src_pts_is_none(raw):
    # ESPN's appliedTotal is 0 in this view; we compute points ourselves.
    henry = next(r for r in parse_projections(raw, season=2024) if r["native_id"] == "3043078")
    assert henry["src_pts_ppr"] is None
