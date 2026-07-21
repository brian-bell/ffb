"""Tiers: largest-gap splits within a per-position draftable pool."""

from ffb.tiers import assign_tiers


def _p(key, points):
    return {"player_key": key, "points": points}


def test_splits_at_largest_gaps_and_overflows_beyond_pool():
    # 8 players, pool depth 6, 3 tiers. Big drops after 96 (->70) and 68 (->50).
    rows = [
        _p("a", 100.0),
        _p("b", 98.0),
        _p("c", 96.0),
        _p("d", 70.0),
        _p("e", 68.0),
        _p("f", 50.0),
        _p("g", 40.0),
        _p("h", 30.0),
    ]
    out = assign_tiers(rows, tier_count=3, pool_size=6)
    assert [r["tier"] for r in out] == [1, 1, 1, 2, 2, 3, 4, 4]


def test_tie_break_prefers_earlier_gap():
    # All gaps equal (10). One boundary needed -> the earliest gap wins.
    rows = [_p("a", 100.0), _p("b", 90.0), _p("c", 80.0), _p("d", 70.0)]
    out = assign_tiers(rows, tier_count=2, pool_size=4)
    assert [r["tier"] for r in out] == [1, 2, 2, 2]


def test_returns_rows_sorted_by_points_desc():
    rows = [_p("low", 50.0), _p("high", 90.0), _p("mid", 70.0)]
    out = assign_tiers(rows, tier_count=1, pool_size=10)
    assert [r["player_key"] for r in out] == ["high", "mid", "low"]
    assert [r["tier"] for r in out] == [1, 1, 1]  # single tier -> no splits
