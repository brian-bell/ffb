"""Rankings: join stored projections with scoring, sort, filter, limit."""

from ffb.rankings import ranked


def test_ranks_rbs_by_ppr_desc(seeded_store):
    rows = ranked(seeded_store, season=2024, position="RB")
    names = [r["full_name"] for r in rows]
    # McCaffrey (26+6+55+42+... ) outscores Henry outscores Taylor.
    assert names == ["Christian McCaffrey", "Derrick Henry", "Jonathan Taylor"]
    assert rows[0]["rank"] == 1
    assert rows[1]["rank"] == 2
    assert rows[0]["points"] > rows[1]["points"] > rows[2]["points"]


def test_points_are_computed_not_sourced(seeded_store):
    # Our computed PPR for Henry, not Sleeper's src_pts_ppr (288).
    henry = next(
        r
        for r in ranked(seeded_store, season=2024, position="RB")
        if r["full_name"] == "Derrick Henry"
    )
    # 1575*.1 + 15*6 + 1*2(rush_2pt) + 20*1 + 145*.1 + 1*6 + 1*-2(fum)
    # = 157.5 + 90 + 2 + 20 + 14.5 + 6 - 2 = 288.0
    assert henry["points"] == 288.0


def test_limit_caps_results(seeded_store):
    rows = ranked(seeded_store, season=2024, position="RB", limit=2)
    assert len(rows) == 2


def test_no_position_returns_all_positions(seeded_store):
    rows = ranked(seeded_store, season=2024)
    positions = {r["position"] for r in rows}
    assert positions == {"RB", "QB", "WR", "K", "DEF"}
    # Ranks are contiguous across the whole set.
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))


def test_empty_season_returns_empty(seeded_store):
    assert ranked(seeded_store, season=1999) == []
