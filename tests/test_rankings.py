"""Rankings: join stored projections with scoring, sort, filter, limit."""

import pytest

from ffb.ingest import resolve_rows
from ffb.rankings import ranked


def _projection(native_id, key, name, *, matched):
    return {
        "player_key": key,
        "native_id": native_id,
        "full_name": name,
        "position": "QB",
        "team": None,
        "matched": matched,
        "season": 2024,
        "source": "sleeper",
        "scope": "season",
        "stats": {"pass_yd": 4000.0},
        "src_pts_ppr": None,
    }


def test_ranked_excludes_unmatched_projection_rows(store):
    store.upsert_projections(
        [
            _projection("1", "qb:1", "Josh Allen", matched=True),
            _projection("tqb:atl", "espn:tqb:atl", "Falcons TQB", matched=False),
        ]
    )

    rows = ranked(store, season=2024, position="QB")

    assert [row["full_name"] for row in rows] == ["Josh Allen"]


@pytest.fixture
def ranked_store(store, sample_rows):
    store.upsert_crosswalk(
        [
            {
                "player_key": f"test:{row['native_id']}",
                "sleeper_id": row["native_id"],
                "full_name": row["full_name"],
                "position": row["position"],
                "team": row["team"],
            }
            for row in sample_rows
            if row["position"] != "DEF"
        ]
    )
    resolved, _ = resolve_rows(store, sample_rows, "sleeper")
    store.upsert_projections(resolved)
    return store


def test_ranks_rbs_by_ppr_desc(ranked_store):
    rows = ranked(ranked_store, season=2024, position="RB")
    names = [r["full_name"] for r in rows]
    # McCaffrey (26+6+55+42+... ) outscores Henry outscores Taylor.
    assert names == ["Christian McCaffrey", "Derrick Henry", "Jonathan Taylor"]
    assert rows[0]["rank"] == 1
    assert rows[1]["rank"] == 2
    assert rows[0]["points"] > rows[1]["points"] > rows[2]["points"]


def test_points_are_computed_not_sourced(ranked_store):
    # Our computed PPR for Henry, not Sleeper's src_pts_ppr (288).
    henry = next(
        r
        for r in ranked(ranked_store, season=2024, position="RB")
        if r["full_name"] == "Derrick Henry"
    )
    # 1575*.1 + 15*6 + 1*2(rush_2pt) + 20*1 + 145*.1 + 1*6 + 1*-2(fum)
    # = 157.5 + 90 + 2 + 20 + 14.5 + 6 - 2 = 288.0
    assert henry["points"] == 288.0


def test_limit_caps_results(ranked_store):
    rows = ranked(ranked_store, season=2024, position="RB", limit=2)
    assert len(rows) == 2


def test_no_position_returns_all_positions(ranked_store):
    rows = ranked(ranked_store, season=2024)
    positions = {r["position"] for r in rows}
    assert positions == {"RB", "QB", "WR", "K", "DEF"}
    # Ranks are contiguous across the whole set.
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))


def test_empty_season_returns_empty(ranked_store):
    assert ranked(ranked_store, season=1999) == []
