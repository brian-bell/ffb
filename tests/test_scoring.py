"""PPR scoring is the deterministic core; hand-computed fixtures first."""

from ffb.config import DEFAULT_PPR, ScoringConfig
from ffb.scoring import ppr_points


def test_empty_stats_score_zero():
    assert ppr_points({}) == 0.0


def test_receiving_rb_hand_computed():
    # 100 rush yd (10) + 1 rush td (6) + 5 rec (5) + 50 rec yd (5) = 26.0
    stats = {"rush_yd": 100, "rush_td": 1, "rec": 5, "rec_yd": 50}
    assert ppr_points(stats) == 26.0


def test_qb_line_hand_computed():
    # 300 pass yd (12) + 2 pass td (8) + 1 int (-2) + 20 rush yd (2) = 20.0
    stats = {"pass_yd": 300, "pass_td": 2, "pass_int": 1, "rush_yd": 20}
    assert ppr_points(stats) == 20.0


def test_fumble_and_two_point_conversions():
    # 1 fum_lost (-2) + 1 rush_2pt (2) + 1 rec (1) = 1.0
    stats = {"fum_lost": 1, "rush_2pt": 1, "rec": 1}
    assert ppr_points(stats) == 1.0


def test_unknown_stats_are_ignored():
    stats = {"rec": 3, "made_up_stat": 999, "adp_ppr": 20.5}
    assert ppr_points(stats) == 3.0


def test_custom_config_overrides_default():
    # Half-PPR: rec worth 0.5. 4 rec -> 2.0
    half = ScoringConfig(weights={"rec": 0.5})
    assert ppr_points({"rec": 4}, half) == 2.0


def test_default_config_is_full_ppr():
    assert DEFAULT_PPR.weights["rec"] == 1.0
