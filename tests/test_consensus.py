"""Consensus: per-source scored points pivoted and averaged per player."""

from ffb.consensus import consensus_rows


def _row(source, native_id, key, name, pos, stats, matched=True):
    return {
        "player_key": key,
        "native_id": native_id,
        "full_name": name,
        "position": pos,
        "team": None,
        "matched": matched,
        "season": 2024,
        "source": source,
        "scope": "season",
        "stats": stats,
        "src_pts_ppr": None,
    }


def test_consensus_averages_source_points_and_counts_n(store):
    # Same canonical key from both sources -> one consensus row, n=2.
    store.upsert_projections(
        [
            _row("sleeper", "3198", "12626", "Derrick Henry", "RB", {"rush_yd": 1000.0}),
            _row("espn", "3043078", "12626", "Derrick Henry", "RB", {"rush_yd": 1200.0}),
        ]
    )
    henry = next(r for r in consensus_rows(store, 2024) if r["player_key"] == "12626")
    assert henry["source_points"] == {"sleeper": 100.0, "espn": 120.0}
    assert henry["consensus"] == 110.0
    assert henry["n"] == 2


def test_single_source_consensus_is_that_source(store):
    store.upsert_projections(
        [
            _row("espn", "999", "espn:999", "Solo Guy", "WR", {"rec_yd": 1000.0}, matched=False),
        ]
    )
    solo = next(r for r in consensus_rows(store, 2024) if r["full_name"] == "Solo Guy")
    assert solo["n"] == 1
    assert solo["consensus"] == 100.0
    assert solo["source_points"] == {"espn": 100.0}


def test_consensus_rows_ranked_by_consensus_desc(store):
    store.upsert_projections(
        [
            _row("sleeper", "1", "a", "Big", "RB", {"rush_yd": 2000.0}),
            _row("sleeper", "2", "b", "Small", "RB", {"rush_yd": 500.0}),
        ]
    )
    rows = consensus_rows(store, 2024, position="RB")
    assert [r["full_name"] for r in rows] == ["Big", "Small"]
    assert [r["rank"] for r in rows] == [1, 2]
    assert rows[0]["consensus"] > rows[1]["consensus"]


def test_sources_filter_restricts_contributors(store):
    # Both sources stored, but a caller can ask for a subset so output doesn't
    # depend on what a prior run happened to persist.
    store.upsert_projections(
        [
            _row("sleeper", "3198", "12626", "Derrick Henry", "RB", {"rush_yd": 1000.0}),
            _row("espn", "3043078", "12626", "Derrick Henry", "RB", {"rush_yd": 1200.0}),
        ]
    )
    sleeper_only = next(
        r for r in consensus_rows(store, 2024, sources=["sleeper"]) if r["player_key"] == "12626"
    )
    assert sleeper_only["n"] == 1
    assert sleeper_only["source_points"] == {"sleeper": 100.0}
    assert sleeper_only["consensus"] == 100.0

    both = next(
        r
        for r in consensus_rows(store, 2024, sources=["sleeper", "espn"])
        if r["player_key"] == "12626"
    )
    assert both["n"] == 2
    assert both["consensus"] == 110.0


def test_position_filter_applies(store):
    store.upsert_projections(
        [
            _row("sleeper", "1", "a", "A Back", "RB", {"rush_yd": 1000.0}),
            _row("sleeper", "2", "b", "A End", "TE", {"rec_yd": 1000.0}),
        ]
    )
    rbs = consensus_rows(store, 2024, position="RB")
    assert {r["position"] for r in rbs} == {"RB"}
