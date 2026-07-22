"""Parsing raw ESPN player objects into normalized projection rows."""

import json
from pathlib import Path

import pytest

from ffb.config import LEAGUE_SCORING
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


def test_decodes_real_kicker_projection(raw):
    tucker = next(r for r in parse_projections(raw, season=2024) if r["native_id"] == "15683")

    assert tucker["position"] == "K"
    assert tucker["stats"] == {
        "fgm_50p": 4.622432766,
        "fgm_40_49": 7.461598239,
        "fgm_30_39": 16.46551991,
        "xpm": 43.46817787,
    }
    assert (
        round(sum(value for key, value in tucker["stats"].items() if key.startswith("fgm_"))) == 29
    )
    assert round(tucker["stats"]["xpm"]) == 43
    assert ppr_points(tucker["stats"], LEAGUE_SCORING) > 0


def test_decodes_and_normalizes_real_team_defense_projection(raw):
    defense = next(r for r in parse_projections(raw, season=2024) if r["native_id"] == "-16025")

    assert defense["position"] == "DEF"
    assert defense["team"] == "SFO"
    assert defense["stats"] == pytest.approx(
        {
            "pts_allow_0": 0.291929508,
            "pts_allow_1_6": 1.319214979,
            "pts_allow_7_13": 4.357664964,
            "pts_allow_14_20": 6.07689997,
            "pts_allow_21_27": 2.97036147,
            "pts_allow_28_34": 1.59156904,
            "pts_allow_35p": 0.392360068,
            "def_fum_td": 1.41976895,
            "int": 14.82473927,
            "fum_rec": 8.768537035,
            "blk_kick": 1.717854627,
            "safe": 0.214530562,
            "sack": 42.86403207,
            "def_kr_td": 0.380187564,
            "pr_td": 0.171,
            "pass_int_td": 0.562739773,
        }
    )
    assert ppr_points(defense["stats"], LEAGUE_SCORING) > 0


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


def test_skips_rows_with_no_scorable_stats():
    # Rows carrying only attempts/aggregate totals still have nothing scorable.
    # They must be skipped, not emitted as 0-point source contributions.
    raw = [
        {
            "id": 15000,
            "fullName": "Some Kicker",
            "defaultPositionId": 5,  # K
            "stats": [{"statSourceId": 1, "scoringPeriodId": 0, "stats": {"75": 20.0, "87": 5.0}}],
        },
        {
            "id": 16000,
            "fullName": "Some D/ST",
            "defaultPositionId": 16,  # D/ST
            "stats": [
                {"statSourceId": 1, "scoringPeriodId": 0, "stats": {"100": 3.0, "120": 10.0}}
            ],
        },
    ]
    assert parse_projections(raw, season=2024) == []
