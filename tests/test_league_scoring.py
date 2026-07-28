"""Rankings use the confirmed Yahoo league scoring, not generic PPR.

These tests lock the hand-entered league settings and prove scoring is
config-driven end to end: changing settings changes ranking order, so rescoring
does not require re-ingestion.
"""

from ffb import config
from ffb.config import DEFAULT_PPR, ScoringConfig
from ffb.consensus import consensus_rows
from ffb.scoring import ppr_points


def test_league_scoring_uses_yahoo_half_ppr_and_twenty_yard_passing():
    weights = config.LEAGUE_SCORING.weights
    assert weights["rec"] == 0.5
    assert weights["pass_yd"] == 0.05


def test_unconfigured_yahoo_turnover_penalties_do_not_score():
    assert (
        ppr_points(
            {"pass_int": 2, "fum_lost": 1},
            config.LEAGUE_SCORING,
        )
        == 0.0
    )
    assert DEFAULT_PPR.weights["pass_int"] == -2.0


def test_qb_line_hand_computed_under_league_scoring():
    # 300 pass yd (15) + 3 pass td (12) + no interception penalty
    # + 10 rush yd (1) = 28.0.
    stats = {"pass_yd": 300, "pass_td": 3, "pass_int": 2, "rush_yd": 10}
    assert ppr_points(stats, config.LEAGUE_SCORING) == 28.0


def test_unconfigured_kicking_stats_do_not_score():
    stats = {"fgm_30_39": 1, "fgm_40_49": 2, "fgm_50p": 1, "xpm": 3}
    assert ppr_points(stats, config.LEAGUE_SCORING) == 0.0


def test_defense_line_hand_computed_under_league_scoring():
    # 4 sack (4) + 2 int (4) + 1 fum_rec (2) + 1 def TD (6) + 1 game at 0 pts (10) = 26.0
    stats = {"sack": 4, "int": 2, "fum_rec": 1, "def_fum_td": 1, "pts_allow_0": 1}
    assert ppr_points(stats, config.LEAGUE_SCORING) == 26.0


def test_return_touchdowns_score_only_on_the_defense_row():
    assert ppr_points({"def_ret_td": 1}, config.LEAGUE_SCORING) == 6.0
    assert ppr_points({"pr_td": 1, "def_kr_td": 1}, config.LEAGUE_SCORING) == 0.0
    # Pre-normalization D/ST rows remain compatible at read time, while an
    # offensive returner carrying those source fields remains unscored.
    assert ppr_points({"pr_td": 1, "def_kr_td": 1}, config.LEAGUE_SCORING, position="DEF") == 12.0


def test_uncommon_visible_touchdown_return_rules_score():
    stats = {"fum_rec_td": 1, "def_2pt": 1}
    assert ppr_points(stats, config.LEAGUE_SCORING) == 8.0


def test_unconfigured_high_points_allowed_bands_do_not_score():
    assert (
        ppr_points(
            {"pts_allow_28_34": 1, "pts_allow_35p": 1},
            config.LEAGUE_SCORING,
        )
        == 0.0
    )


def test_defensive_int_distinct_from_quarterback_interception():
    # A defensive interception scores two while a thrown interception has no
    # configured penalty. The source keys must remain distinct.
    assert ppr_points({"int": 1}, config.LEAGUE_SCORING) == 2.0
    assert ppr_points({"pass_int": 1}, config.LEAGUE_SCORING) == 0.0


def test_roster_slots_match_yahoo_league():
    assert config.LEAGUE_ROSTER_SLOTS == {
        "QB": 1,
        "WR": 1,
        "RB": 1,
        "TE": 1,
        "W/T": 1,
        "W/R/T": 2,
        "DEF": 1,
        "BN": 8,
    }


def test_league_has_ten_teams():
    assert config.LEAGUE_NUM_TEAMS == 10


def _seed_qb(store, key, name, stats):
    store.upsert_projections(
        [
            {
                "player_key": key,
                "full_name": name,
                "position": "QB",
                "team": "FA",
                "matched": True,
                "season": 2024,
                "source": "sleeper",
                "scope": "season",
                "native_id": key,
                "stats": stats,
                "src_pts_ppr": None,
            }
        ]
    )


def test_changing_settings_changes_ranking_order(store):
    # A gunslinger (more TDs, more INTs) vs. a caretaker (fewer TDs, no INTs).
    _seed_qb(store, "gun", "Gunslinger", {"pass_td": 6, "pass_int": 3})
    _seed_qb(store, "care", "Caretaker", {"pass_td": 5, "pass_int": 0})

    lenient = ScoringConfig(weights={"pass_td": 4.0, "pass_int": -1.0})
    harsh = ScoringConfig(weights={"pass_td": 4.0, "pass_int": -4.0})

    lenient_order = [r["full_name"] for r in consensus_rows(store, 2024, cfg=lenient)]
    harsh_order = [r["full_name"] for r in consensus_rows(store, 2024, cfg=harsh)]

    # Lenient: 24-3=21 > 20. Harsh: 24-12=12 < 20. The penalty flips the order.
    assert lenient_order == ["Gunslinger", "Caretaker"]
    assert harsh_order == ["Caretaker", "Gunslinger"]
