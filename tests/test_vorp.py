"""VORP: greedy league-wide starter fill -> per-position replacement baseline."""

from ffb.vorp import attach_vorp, replacement_levels

# Toy league: 2 teams, tiny lineup with one flex, so the fill is hand-checkable.
ROSTER = {"RB": 1, "WR": 1, "W/R/T": 1, "QB": 1, "BN": 1}
NUM_TEAMS = 2


def _p(key, pos, points):
    return {"player_key": key, "position": pos, "points": points}


# RB/WR deep enough to overflow starters + flex; QB exactly fills its 2 slots.
POOL = [
    _p("r1", "RB", 100.0),
    _p("r2", "RB", 90.0),
    _p("r3", "RB", 80.0),
    _p("r4", "RB", 70.0),
    _p("r5", "RB", 60.0),
    _p("w1", "WR", 95.0),
    _p("w2", "WR", 85.0),
    _p("w3", "WR", 75.0),
    _p("w4", "WR", 65.0),
    _p("q1", "QB", 50.0),
    _p("q2", "QB", 40.0),
]


def test_replacement_level_respects_flex_absorption():
    # Dedicated RB=2, WR=2 (2 teams x 1); flex=2 absorbs the next best RB/WR.
    # Best remaining unassigned: RB4=70 (r4), WR4=65 (w4).
    repl = replacement_levels(POOL, ROSTER, NUM_TEAMS)
    assert repl["RB"] == 70.0
    assert repl["WR"] == 65.0


def test_exhausted_position_has_zero_replacement():
    # Only 2 QBs for 2 QB slots -> none left over -> baseline 0.0.
    repl = replacement_levels(POOL, ROSTER, NUM_TEAMS)
    assert repl.get("QB", 0.0) == 0.0


def test_attach_vorp_values_including_negative_below_baseline():
    rows = {r["player_key"]: r for r in attach_vorp(POOL, ROSTER, NUM_TEAMS)}
    assert rows["r1"]["vorp"] == 30.0  # 100 - 70
    assert rows["r4"]["vorp"] == 0.0  # exactly at baseline
    assert rows["r5"]["vorp"] == -10.0  # below the baseline -> negative
    assert rows["w1"]["vorp"] == 30.0  # 95 - 65
    assert rows["q1"]["vorp"] == 50.0  # exhausted position -> baseline 0
