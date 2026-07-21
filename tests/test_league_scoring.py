"""Slice 4: rankings use the *league's* scoring, not generic PPR.

``config.LEAGUE_SCORING`` is a hand-entered placeholder (typical Yahoo full-PPR)
standing in until slice 2 loads the real settings from the Yahoo league into the
store. These tests lock the placeholder's semantics and — more importantly —
prove scoring is config-driven end to end: changing the settings changes the
ranking order, which is the whole promise of "re-score to league settings is a
config swap, not a re-ingest."
"""

from ffb import config
from ffb.config import DEFAULT_PPR, ScoringConfig
from ffb.consensus import consensus_rows
from ffb.scoring import ppr_points


def test_league_scoring_is_full_ppr_placeholder():
    assert config.LEAGUE_SCORING.weights["rec"] == 1.0


def test_league_settings_differ_from_generic_default():
    # The point of slice 4: league scoring is its own thing, distinct from the
    # library's generic default. Yahoo's default interception is -1; the generic
    # DEFAULT_PPR uses -2. If these ever coincide the "uses league scoring" wiring
    # would be untestable, so assert they diverge.
    assert config.LEAGUE_SCORING.weights["pass_int"] == -1.0
    assert DEFAULT_PPR.weights["pass_int"] == -2.0


def test_qb_line_hand_computed_under_league_scoring():
    # 300 pass yd (12) + 3 pass td (12) + 2 int (-2) + 10 rush yd (1) = 23.0
    stats = {"pass_yd": 300, "pass_td": 3, "pass_int": 2, "rush_yd": 10}
    assert ppr_points(stats, config.LEAGUE_SCORING) == 23.0


def test_kicker_line_hand_computed_under_league_scoring():
    # 1 FG 30-39 (3) + 2 FG 40-49 (8) + 1 FG 50+ (5) + 3 XP (3) = 19.0
    stats = {"fgm_30_39": 1, "fgm_40_49": 2, "fgm_50p": 1, "xpm": 3}
    assert ppr_points(stats, config.LEAGUE_SCORING) == 19.0


def test_defense_line_hand_computed_under_league_scoring():
    # 4 sack (4) + 2 int (4) + 1 fum_rec (2) + 1 def TD (6) + 1 game at 0 pts (10) = 26.0
    stats = {"sack": 4, "int": 2, "fum_rec": 1, "def_fum_td": 1, "pts_allow_0": 1}
    assert ppr_points(stats, config.LEAGUE_SCORING) == 26.0


def test_return_touchdowns_scored_on_the_returners_row():
    # Sleeper puts kick/punt-return TDs on the returner's own offensive row
    # (def_kr_td, pr_td), e.g. Turpin/Davis. Yahoo awards return TDs, so they must
    # score 6 — not silently 0 under a mis-keyed def_pr_td.
    assert ppr_points({"pr_td": 1}, config.LEAGUE_SCORING) == 6.0
    assert ppr_points({"def_kr_td": 1}, config.LEAGUE_SCORING) == 6.0
    # The fabricated keys are gone (no double-count under a phantom def_pr_td).
    assert "def_pr_td" not in config.LEAGUE_SCORING.weights


def test_points_allowed_ladder_penalizes_blowouts():
    # A game allowing 35+ points is a net negative under the band ladder — a K/DEF
    # projection is no longer silently zero.
    assert ppr_points({"pts_allow_35p": 1}, config.LEAGUE_SCORING) == -4.0


def test_defensive_int_distinct_from_quarterback_interception():
    # 'int' (defense makes a pick, +2) must not be confused with 'pass_int' (a QB
    # throwing one, -1). They are different keys, so one flat map is safe.
    assert ppr_points({"int": 1}, config.LEAGUE_SCORING) == 2.0
    assert ppr_points({"pass_int": 1}, config.LEAGUE_SCORING) == -1.0


def test_roster_slots_placeholder_uses_yahoo_abbreviations():
    # Yahoo labels the flex "W/R/T" and defense "DEF"; use those now so slice 2's
    # store-loaded slots drop in without renaming.
    slots = config.LEAGUE_ROSTER_SLOTS
    assert slots["W/R/T"] == 1
    assert slots["DEF"] == 1
    assert sum(slots.values()) == 15  # 9 starters + 6 bench, typical 12-team


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
