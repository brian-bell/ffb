"""Walk-forward backtest, promotion gate, and shadow win probabilities."""

import math

import pytest

from ffb.backtest import (
    MAE_WORSE_TOLERANCE,
    MIN_BACKTEST_WEEK,
    MIN_GATE_WEEKS,
    REASON_BRIER,
    REASON_FEW_LEAGUES,
    REASON_FEW_WEEKS,
    REASON_NO_BRIER,
    REASON_NO_REGRET,
    REASON_PER_LEAGUE,
    mae_reason,
    walk_forward_backtest,
)
from ffb.calibration import (
    CLOSE_CALL_PROBABILITY,
    fit_calibration,
    probability_close_calls,
    win_probability,
)
from ffb.lineup import compare_lineup
from ffb.predlog import build_prediction_ledger
from ffb.retro import build_lineup_snapshot, retro_report, shadow_report

YAHOO = "yahoo:470.l.928421"
SLEEPER = "sleeper:1395854363380965376"
LEAGUES = (YAHOO, SLEEPER)


def test_gate_constants_match_the_spec():
    assert MIN_BACKTEST_WEEK == 3
    assert MIN_GATE_WEEKS == 6
    assert MAE_WORSE_TOLERANCE == 0.3
    assert CLOSE_CALL_PROBABILITY == 0.6


def test_win_probability_is_a_normal_tail_and_is_certain_without_spread():
    assert win_probability(10, 0, 9, 0) == 1.0
    assert win_probability(9, 0, 10, 0) == 0.0
    assert win_probability(10, 0, 10, 0) == 0.5
    assert win_probability(10, 2, 10, 3) == 0.5
    expected = 0.5 * (1.0 + math.erf(1 / math.sqrt(2)))
    assert win_probability(1, 1, 0, 0) == pytest.approx(expected)
    assert win_probability(float("nan"), 1, 0, 1) is None


def test_probability_close_calls_sit_beside_the_point_gap_rule():
    players = [
        _bench_player("a", "Alpha", 10, 10, "RB"),
        _bench_player("b", "Bravo", 9, 10, "BN"),
        _bench_player("c", "Charlie", 7, 20, "BN"),
    ]
    report = compare_lineup(players, {"RB": 1, "BN": 3})

    point_names = [row["name"] for row in report["close_calls"]]
    probability_names = [row["name"] for row in probability_close_calls(players, report["optimal"])]

    assert point_names == ["Bravo"]
    assert probability_names == ["Bravo", "Charlie"]
    calls = probability_close_calls(players, report["optimal"])
    assert all(row["p_win"] < CLOSE_CALL_PROBABILITY for row in calls)


def test_a_certain_gap_is_not_a_probability_close_call():
    players = [
        _bench_player("a", "Alpha", 10, 0, "RB"),
        _bench_player("b", "Bravo", 8, 0, "BN"),
    ]
    report = compare_lineup(players, {"RB": 1, "BN": 2})

    assert report["close_calls"] == []
    assert probability_close_calls(players, report["optimal"]) == []


def test_missing_sd_skips_the_probability_rule():
    players = [
        _bench_player("a", "Alpha", 10, None, "RB"),
        _bench_player("b", "Bravo", 9, None, "BN"),
    ]
    report = compare_lineup(players, {"RB": 1, "BN": 2})

    assert report["close_calls"]
    assert probability_close_calls(players, report["optimal"]) == []


def test_shadow_reports_both_close_call_rules_without_publishing_them():
    players = [
        _ledger_player("a", "Alpha", 10, 10, "RB"),
        _ledger_player("b", "Bravo", 9, 10, "BN"),
        _ledger_player("c", "Charlie", 7, 20, "BN"),
    ]
    advice = _advice(players)
    ledger = build_prediction_ledger(
        season=2026,
        week=3,
        league_key=YAHOO,
        user_team_key="t.1",
        generated_at="2026-10-01T00:00:00Z",
        scoring_weights={"rec": 1.0},
        projection_sources={},
        players=players,
        calibration={"params_key": "calibration/2026_week3", "sha256": "abc"},
    )

    shadow = shadow_report(advice, _actuals(players), ledger)
    published = retro_report(advice, _actuals(players))

    assert [row["name"] for row in shadow["close_calls"]] == ["Bravo"]
    assert [row["name"] for row in shadow["probability_close_calls"]] == ["Bravo", "Charlie"]
    assert "probability_close_calls" not in published
    assert "close_calls" not in published


def test_league_brier_counts_every_teams_slot_pairs():
    facts = [
        _fact(YAHOO, 3, "user-a", consensus=12, actual=0, user=True, selected="RB"),
        _fact(YAHOO, 3, "user-b", consensus=10, actual=14, user=True, selected="BN"),
        _fact(YAHOO, 3, "opp-a", consensus=8, actual=1, selected="RB"),
        _fact(YAHOO, 3, "opp-b", consensus=6, actual=9, selected="BN"),
    ]

    report = walk_forward_backtest(facts, season=2026, roster_slots=_slots((YAHOO,), weeks=[3]))

    assert report["metrics"]["brier"]["decisions"]["raw"]["n"] == 1
    assert report["metrics"]["brier"]["league"]["raw"]["n"] == 2


def test_empty_history_never_promotes():
    report = walk_forward_backtest([], season=2026)

    assert report["weeks"] == []
    assert report["gate"]["passed"] is False
    assert report["gate"]["n_weeks"] == 0
    assert report["gate"]["reasons"] == [
        REASON_FEW_WEEKS,
        REASON_FEW_LEAGUES,
        REASON_NO_REGRET,
        REASON_NO_BRIER,
    ]


def test_thin_history_reproduces_raw_advice_and_does_not_promote():
    facts = _upsets()
    report = walk_forward_backtest(facts, season=2026, roster_slots=_slots())

    assert report["gate"]["passed"] is False
    assert report["gate"]["reasons"] == [REASON_BRIER]
    assert report["metrics"]["regret"]["raw"] == report["metrics"]["regret"]["calibrated"] == 14
    assert report["metrics"]["brier"]["decisions"]["raw"]["brier"] == 1
    assert report["metrics"]["brier"]["decisions"]["calibrated"]["brier"] == 1
    week3 = _week(report, 3)
    week8 = _week(report, 8)
    assert week3["fit_weeks"] == []
    assert week8["fit_weeks"] == [3, 4, 5, 6, 7]
    assert _walk_forward(report)
    assert report["bootstrap"]["regret_delta"]["low"] == 0
    assert report["bootstrap"]["regret_delta"]["high"] == 0
    assert report["bootstrap"]["brier_delta"]["low"] == 0
    assert report["bootstrap"]["brier_delta"]["high"] == 0


def test_scored_week_is_fit_with_later_facts_still_visible_to_the_fit(monkeypatch):
    facts = _leak_facts()
    seen = []
    real = fit_calibration

    def spy(rows, *, season, week, league_key):
        seen.append((week, max(row["week"] for row in rows), [row["season"] for row in rows]))
        return real(rows, season=season, week=week, league_key=league_key)

    monkeypatch.setattr("ffb.backtest.fit_calibration", spy)
    report = walk_forward_backtest(facts, season=2026, roster_slots=_slots((YAHOO,)))

    call = next(item for item in seen if item[0] == 3)
    assert call[1] >= 5
    assert 2025 not in call[2]
    week3 = _week(report, 3)
    assert week3["fit_weeks"] == [1]
    assert week3["parameters"]["RB"]["n"] == 100
    assert week3["parameters"]["RB"]["bias"] == 0.0
    assert _walk_forward(report)
    direct = fit_calibration(facts, season=2026, week=3, league_key="pooled")
    assert week3["parameters"]["RB"]["bias"] == direct["parameters"]["RB"]["bias"]
    assert week3["n_by_position"] == direct["n_by_position"]


def test_gate_passes_when_regret_holds_brier_improves_and_mae_stays_inside_0_3():
    facts = _pass_facts()
    report = walk_forward_backtest(facts, season=2026, roster_slots=_slots())
    again = walk_forward_backtest(facts, season=2026, roster_slots=_slots())

    assert report["gate"]["passed"] is True
    assert report["gate"]["reasons"] == []
    assert report["gate"]["n_weeks"] == 6
    assert report["gate"]["n_leagues"] == 2
    assert report["leagues"] == sorted(LEAGUES)
    assert report["bootstrap"] == again["bootstrap"]
    week3 = _week(report, 3)
    assert week3["fit_weeks"] == [1]
    assert week3["parameters"]["RB"]["n"] == 200
    assert week3["parameters"]["RB"]["bias"] == 0.0
    assert week3["parameters"]["RB"]["sd_a"] == pytest.approx(10 / 3)
    assert _walk_forward(report)
    for row in report["weeks"]:
        for league in row["league_regret"]:
            assert league["raw"] == 14
            assert league["calibrated"] == 14
    decisions = report["metrics"]["brier"]["decisions"]
    league_pairs = report["metrics"]["brier"]["league"]
    assert decisions["raw"]["brier"] == 1
    assert decisions["raw"]["n"] == 12
    assert decisions["calibrated"]["brier"] < 1
    assert league_pairs["raw"]["n"] == 12
    assert league_pairs["calibrated"]["brier"] < league_pairs["raw"]["brier"]
    assert report["metrics"]["decision_accuracy"]["raw"]["weighted_hit_rate"] == 0
    assert report["metrics"]["spearman"]["raw"] == -1
    assert report["metrics"]["spearman"]["n_groups"] == 12
    rb = [row for row in report["metrics"]["model_position"] if row["position"] == "RB"]
    assert rb[0]["n"] == 24
    assert abs(rb[0]["mae_calibrated"] - rb[0]["mae_raw"]) <= MAE_WORSE_TOLERANCE
    sources = [row for row in report["metrics"]["source_position"] if row["position"] == "RB"]
    assert {row["source"] for row in sources} == {"espn", "sleeper"}
    for row in sources:
        assert row["n"] == 24
        assert row["mae"] == 8
        assert row["bias"] == 4
        assert row["rmse"] == round(math.sqrt(80), 4)
        assert row["n_without_dnp"] == 12
        assert row["mae_without_dnp"] == 4
        assert row["bias_without_dnp"] == -4
    interval = report["bootstrap"]["brier_delta"]
    assert interval["mean"] < 0
    assert interval["low"] <= interval["mean"] <= interval["high"]


def test_one_league_and_the_per_league_variant_never_promote():
    facts = _pass_facts()
    yahoo_only = [fact for fact in facts if fact["league_key"] == YAHOO]
    one = walk_forward_backtest(yahoo_only, season=2026, roster_slots=_slots((YAHOO,)))
    variant = walk_forward_backtest(
        facts, season=2026, roster_slots=_slots(), league_key=YAHOO, pooled=False
    )

    assert one["gate"]["passed"] is False
    assert one["gate"]["reasons"] == [REASON_FEW_LEAGUES]
    assert variant["gate"]["passed"] is False
    assert variant["gate"]["reasons"] == [REASON_PER_LEAGUE, REASON_FEW_LEAGUES]
    assert variant["leagues"] == [YAHOO]
    assert _week(variant, 3)["parameters"]["RB"]["n"] == 100
    with pytest.raises(ValueError, match="league_key"):
        walk_forward_backtest(facts, season=2026, pooled=False)


def test_position_mae_worse_than_0_3_fails_an_otherwise_passing_gate():
    facts = _pass_facts() + _wr_drag()
    report = walk_forward_backtest(facts, season=2026, roster_slots=_slots())

    assert report["gate"]["passed"] is False
    assert report["gate"]["reasons"] == [mae_reason("WR")]


def test_backtest_keeps_one_roster_and_drops_post_kickoff_predictions():
    early = "2026-10-10T11:00:00Z"
    late = "2026-10-11T20:00:00Z"
    facts = [
        _fact(
            YAHOO,
            3,
            "gone",
            consensus=1,
            actual=100,
            generated_at=early,
            nfl_team="KCC",
            user=True,
            selected="BN",
        ),
        _fact(
            YAHOO,
            3,
            "stay",
            consensus=5,
            actual=5,
            generated_at=early,
            nfl_team="KCC",
            user=True,
            selected="RB",
        ),
        _fact(
            YAHOO,
            3,
            "stay",
            consensus=100,
            actual=5,
            generated_at=late,
            nfl_team="KCC",
            user=True,
            selected="RB",
        ),
        _fact(
            YAHOO,
            3,
            "late-only",
            consensus=8,
            actual=8,
            generated_at=late,
            nfl_team="PHI",
            user=True,
            selected="BN",
        ),
    ]

    report = walk_forward_backtest(
        facts,
        season=2026,
        roster_slots=_slots((YAHOO,), weeks=[3]),
        kickoffs={(3, "KCC"): "2026-10-11T17:00:00Z", (3, "PHI"): "2026-10-08T00:20:00Z"},
    )

    assert report["metrics"]["model_position"] == [
        {
            "position": "RB",
            "n": 1,
            "mae_raw": 0.0,
            "rmse_raw": 0.0,
            "bias_raw": 0.0,
            "mae_calibrated": 0.0,
            "rmse_calibrated": 0.0,
            "bias_calibrated": 0.0,
        }
    ]
    assert report["weeks"][0]["league_regret"][0]["raw"] == 0


def test_mae_delta_of_0_3_is_allowed_and_0_31_is_not():
    allowed = walk_forward_backtest(_bias_probe(0.6), season=2026, roster_slots=_slots((YAHOO,)))
    blocked = walk_forward_backtest(_bias_probe(0.62), season=2026, roster_slots=_slots((YAHOO,)))

    allowed_row = allowed["metrics"]["model_position"][0]
    blocked_row = blocked["metrics"]["model_position"][0]
    assert allowed_row["mae_raw"] == 0
    assert allowed_row["mae_calibrated"] == pytest.approx(0.3)
    assert mae_reason("RB") not in allowed["gate"]["reasons"]
    assert blocked_row["mae_calibrated"] == pytest.approx(0.31)
    assert mae_reason("RB") in blocked["gate"]["reasons"]


def _walk_forward(report: dict) -> bool:
    for row in report["weeks"]:
        assert all(fit < row["week"] for fit in row["fit_weeks"])
        assert row["week"] not in row["fit_weeks"]
    return True


def _week(report: dict, week: int) -> dict:
    return next(row for row in report["weeks"] if row["week"] == week)


def _slots(leagues=LEAGUES, weeks=None):
    chosen = range(3, 9) if weeks is None else weeks
    return {(league, week): {"RB": 1, "BN": 2} for league in leagues for week in chosen}


def _pass_facts():
    facts = []
    for league in LEAGUES:
        facts.extend(_balanced(league, 100))
        facts.extend(_upsets((league,)))
    return facts


def _upsets(leagues=LEAGUES):
    facts = []
    for league in leagues:
        for week in range(3, 9):
            facts.append(
                _fact(
                    league,
                    week,
                    f"{league}-a-{week}",
                    consensus=12,
                    actual=0,
                    user=True,
                    selected="RB",
                    calibrated_mean=0,
                )
            )
            facts.append(
                _fact(
                    league,
                    week,
                    f"{league}-b-{week}",
                    consensus=10,
                    actual=14,
                    user=True,
                    selected="BN",
                    calibrated_mean=50,
                )
            )
    return facts


def _balanced(league, count, *, week=1, position="RB", residual=5.0, consensus=10.0):
    rows = []
    for index in range(count):
        sign = 1 if index % 2 == 0 else -1
        rows.append(
            _fact(
                league,
                week,
                f"train-{league}-{position}-{index}",
                consensus=consensus,
                actual=consensus + sign * residual,
                position=position,
            )
        )
    return rows


def _leak_facts():
    facts = _balanced(YAHOO, 100)
    facts.append(
        _fact(
            YAHOO,
            1,
            "other-season",
            consensus=10,
            actual=10000,
            season=2025,
        )
    )
    facts.append(_fact(YAHOO, 3, "week3-user", consensus=10, actual=10, user=True, selected="RB"))
    for index in range(40):
        facts.append(_fact(YAHOO, 5, f"poison-{index}", consensus=10, actual=1000))
    return facts


def _wr_drag():
    rows = [
        _fact(YAHOO, 1, f"wr-train-{index}", consensus=10, actual=5, position="WR")
        for index in range(100)
    ]
    for league in LEAGUES:
        for week in range(3, 9):
            rows.append(
                _fact(
                    league,
                    week,
                    f"wr-{league}-{week}",
                    consensus=10,
                    actual=10,
                    position="WR",
                )
            )
    return rows


def _bias_probe(residual):
    rows = []
    for index in range(100):
        rows.append(
            _fact(
                YAHOO,
                1,
                f"bias-{index}",
                consensus=10,
                actual=10 + residual,
            )
        )
    rows.append(_fact(YAHOO, 3, "scored", consensus=10, actual=10))
    return rows


def _fact(
    league,
    week,
    native_id,
    *,
    consensus,
    actual,
    position="RB",
    user=False,
    selected="BN",
    season=2026,
    calibrated_mean=999.0,
    generated_at="2026-10-01T00:00:00Z",
    nfl_team="KCC",
):
    return {
        "league_key": league,
        "season": season,
        "week": week,
        "native_id": native_id,
        "position": position,
        "nfl_team": nfl_team,
        "team_key": "t.1" if user else "other",
        "is_user_team": user,
        "name": native_id,
        "eligible_positions": [position],
        "source_points": {"espn": consensus, "sleeper": consensus},
        "consensus": consensus,
        "n": 2,
        "calibrated_mean": calibrated_mean,
        "calibrated_sd": 999.0,
        "injury_status": None,
        "selected_position_at_advice": selected,
        "actual_points": actual,
        "actual_selected_position": selected,
        "moved": False,
        "generated_at": generated_at,
    }


def _bench_player(native_id, name, points, sd, selected):
    return {
        "native_id": native_id,
        "name": name,
        "position": "RB",
        "eligible_positions": ["RB"],
        "selected_position": selected,
        "points": points,
        "sd": sd,
    }


def _ledger_player(native_id, name, points, sd, selected):
    return {
        "team_key": "t.1",
        "native_id": native_id,
        "native_player_key": f"1.p.{native_id}",
        "player_key": native_id,
        "projection_key": native_id,
        "matched": True,
        "name": name,
        "position": "RB",
        "team": "BAL",
        "selected_position": selected,
        "eligible_positions": ["RB"],
        "points": points,
        "n": 1,
        "source_points": {"sleeper": points},
        "calibrated_mean": points,
        "calibrated_sd": sd,
    }


def _advice(players):
    roster = {"RB": 1, "BN": 3}
    report = compare_lineup(players, roster)
    return build_lineup_snapshot(
        season=2026,
        week=3,
        generated_at="2026-10-01T00:00:00Z",
        team_key="t.1",
        team_name="Brian",
        roster_slots=roster,
        players=players,
        report=report,
    )


def _actuals(players):
    return {
        "schema_version": 2,
        "source": "yahoo",
        "synced_at": "2026-10-14T12:00:00Z",
        "league": {
            "league_id": "1",
            "league_key": "470.l.928421",
            "name": "Test",
            "season": 2026,
            "week": 3,
            "num_teams": 2,
        },
        "matchups": [
            {
                "matchup_id": "1",
                "week": 3,
                "teams": [
                    {"team_key": "t.1", "points": 10.0},
                    {"team_key": "t.2", "points": 0.0},
                ],
            }
        ],
        "players": [
            {
                "native_id": player["native_id"],
                "native_player_key": player["native_player_key"],
                "name": player["name"],
                "team_key": "t.1",
                "selected_position": player["selected_position"],
                "points": 1.0,
            }
            for player in players
        ],
    }
