"""Prediction-vs-actual outcomes: fact table, decisions, and cross-week history."""

import pytest

from ffb.outcomes import (
    NOTE_NO_KICKOFFS,
    NOTE_UNPAIRED,
    decision_table,
    fact_table,
    history_report,
    paired_advice,
    select_scoring_facts,
)
from ffb.predlog import build_prediction_ledger
from ffb.retro import build_lineup_snapshot, retro_report

STAMP = "2026-10-10T11:02:14Z"
LATER = "2026-10-11T11:02:14Z"
YAHOO = "yahoo:470.l.928421"
SLEEPER = "sleeper:1395854363380965376"


def _ledger_player(**overrides):
    row = {
        "team_key": "t.1",
        "native_id": "4046",
        "native_player_key": "470.p.4046",
        "player_key": "mfl:4046",
        "projection_key": "mfl:4046",
        "matched": True,
        "name": "Some Back",
        "position": "RB",
        "team": "KC",
        "selected_position": "RB",
        "eligible_positions": ["RB", "W/R/T"],
        "points": 10.0,
        "n": 2,
        "source_points": {"sleeper": 12.0, "espn": 8.0},
        "injury": None,
    }
    row.update(overrides)
    return row


def _ledger(players, *, league_key=YAHOO, generated_at=STAMP, week=1, user_team_key="t.1"):
    return build_prediction_ledger(
        season=2026,
        week=week,
        league_key=league_key,
        user_team_key=user_team_key,
        generated_at=generated_at,
        scoring_weights={"rec": 1.0},
        projection_sources={},
        players=players,
    )


def _actual(native_id, *, points, team_key="t.1", selected_position="RB", name="Some Back"):
    return {
        "native_id": native_id,
        "native_player_key": f"p.{native_id}",
        "name": name,
        "team_key": team_key,
        "selected_position": selected_position,
        "points": points,
    }


def _bundle(players, *, source="yahoo", league_key="470.l.928421", week=1):
    team_keys = sorted({row["team_key"] for row in players})
    if len(team_keys) % 2:
        team_keys.append(f"{team_keys[-1]}-opp")
    matchups = []
    for index in range(0, len(team_keys), 2):
        matchups.append(
            {
                "matchup_id": str(index // 2 + 1),
                "week": week,
                "teams": [
                    {"team_key": team_keys[index], "points": 1.0},
                    {"team_key": team_keys[index + 1], "points": 1.0},
                ],
            }
        )
    return {
        "schema_version": 2,
        "source": source,
        "synced_at": "2026-10-14T12:00:00Z",
        "league": {
            "league_id": "1",
            "league_key": league_key,
            "name": "Test",
            "season": 2026,
            "week": week,
            "num_teams": len(team_keys),
        },
        "matchups": matchups,
        "players": players,
    }


def _fact(**overrides):
    row = {
        "league_key": YAHOO,
        "season": 2026,
        "week": 1,
        "native_id": "4046",
        "projection_key": "mfl:4046",
        "position": "RB",
        "nfl_team": "KC",
        "team_key": "t.1",
        "is_user_team": True,
        "name": "Some Back",
        "eligible_positions": ["RB", "W/R/T"],
        "source_points": {"sleeper": 12.0, "espn": 8.0},
        "consensus": 10.0,
        "n": 2,
        "calibrated_mean": None,
        "calibrated_sd": None,
        "injury_status": None,
        "selected_position_at_advice": "RB",
        "recommended_slot": "RB",
        "generated_at": STAMP,
        "hours_before_kickoff": None,
        "actual_points": 8.0,
        "actual_selected_position": "RB",
        "actuals_synced_at": "2026-10-14T12:00:00Z",
        "residual": -2.0,
        "source_residuals": {},
        "dnp_proxy": False,
        "moved": False,
    }
    row.update(overrides)
    return row


def test_fact_table_joins_one_row_per_ledger_player_on_native_id():
    ledger = _ledger(
        [
            _ledger_player(),
            _ledger_player(
                team_key="t.2",
                native_id="99",
                name="Other",
                points=4.0,
                source_points={"sleeper": 4.0},
                n=1,
            ),
        ]
    )
    table = fact_table(
        ledger,
        _bundle([_actual("4046", points=8.0), _actual("99", points=1.0, team_key="t.2")]),
    )

    assert [(row["native_id"], row["generated_at"]) for row in table["facts"]] == [
        ("4046", STAMP),
        ("99", STAMP),
    ]
    mine = table["facts"][0]
    assert mine["league_key"] == YAHOO
    assert mine["season"] == 2026
    assert mine["week"] == 1
    assert mine["projection_key"] == "mfl:4046"
    assert mine["position"] == "RB"
    assert mine["nfl_team"] == "KC"
    assert mine["team_key"] == "t.1"
    assert mine["is_user_team"] is True
    assert mine["consensus"] == 10.0
    assert mine["n"] == 2
    assert mine["source_points"] == {"sleeper": 12.0, "espn": 8.0}
    assert mine["actual_points"] == 8.0
    assert mine["actual_selected_position"] == "RB"
    assert mine["actuals_synced_at"] == "2026-10-14T12:00:00Z"
    assert mine["residual"] == -2.0  # actual − consensus
    assert mine["source_residuals"] == {"sleeper": -4.0, "espn": 0.0}
    assert mine["moved"] is False
    assert mine["dnp_proxy"] is False
    assert mine["calibrated_mean"] is None
    assert mine["calibrated_sd"] is None
    assert mine["hours_before_kickoff"] is None
    assert mine["selected_position_at_advice"] == "RB"
    assert table["missing_actuals"] == []


def test_two_runs_keep_separate_rows_for_the_same_player():
    early = _ledger([_ledger_player(points=10.0)], generated_at=STAMP)
    late = _ledger([_ledger_player(points=11.0)], generated_at=LATER)
    actuals = _bundle([_actual("4046", points=8.0)])

    rows = fact_table(early, actuals)["facts"] + fact_table(late, actuals)["facts"]

    assert [(row["generated_at"], row["consensus"]) for row in rows] == [
        (STAMP, 10.0),
        (LATER, 11.0),
    ]


def test_moved_player_stays_in_the_fact_table():
    ledger = _ledger([_ledger_player(team_key="t.1")])
    table = fact_table(ledger, _bundle([_actual("4046", points=6.0, team_key="t.9")]))

    row = table["facts"][0]
    assert row["moved"] is True
    assert row["team_key"] == "t.1"
    assert row["actual_points"] == 6.0
    assert table["missing_actuals"] == []


def test_missing_actuals_are_reported_and_left_out_of_residuals():
    ledger = _ledger(
        [
            _ledger_player(),
            _ledger_player(native_id="7", name="Gone", player_key="mfl:7", projection_key="mfl:7"),
        ]
    )
    table = fact_table(ledger, _bundle([_actual("4046", points=8.0)]))

    assert table["missing_actuals"] == [{"native_id": "7", "name": "Gone"}]
    missing = table["facts"][1]
    assert missing["actual_points"] is None
    assert missing["residual"] is None
    assert missing["dnp_proxy"] is False
    assert missing["moved"] is False


def test_yahoo_defense_id_joins_canonical_def_key_in_either_direction():
    """100014 is the Rams D/ST. Actuals may say 100014 or def:LAR."""
    ledger = _ledger(
        [
            _ledger_player(
                native_id="100014",
                native_player_key="470.p.100014",
                player_key="def:LAR",
                projection_key="def:LAR",
                name="Rams",
                position="WR",  # noisy Yahoo scrape; the id is still the Rams D/ST
                team=None,
                selected_position="DEF",
                eligible_positions=["DEF"],
                points=5.0,
                n=1,
                source_points={"espn": 5.0},
            )
        ]
    )

    from_canonical = fact_table(
        ledger,
        _bundle([_actual("def:LAR", points=9.0, selected_position="DEF", name="Rams")]),
    )["facts"][0]
    from_yahoo_id = fact_table(
        ledger,
        _bundle([_actual("100014", points=9.0, selected_position="BN", name="Rams")]),
    )["facts"][0]

    for row in (from_canonical, from_yahoo_id):
        assert row["native_id"] == "def:LAR"
        assert row["position"] == "DEF"
        assert row["nfl_team"] == "LAR"
        assert row["actual_points"] == 9.0
        assert row["dnp_proxy"] is False

    already_canonical = _ledger(
        [
            _ledger_player(
                native_id="def:LAR",
                player_key="def:LAR",
                projection_key="def:LAR",
                name="Rams",
                position="DEF",
                team="LAR",
                selected_position="DEF",
                eligible_positions=["DEF"],
                points=5.0,
                n=1,
                source_points={"espn": 5.0},
            )
        ]
    )
    joined = fact_table(
        already_canonical,
        _bundle([_actual("100014", points=4.0, selected_position="DEF", name="Rams")]),
    )["facts"][0]
    assert joined["native_id"] == "def:LAR"
    assert joined["actual_points"] == 4.0


def test_sleeper_defense_joins_team_code_or_canonical_key_without_yahoo_ids():
    player = _ledger_player(
        native_id="LAR",
        native_player_key="sleeper:LAR",
        player_key="def:LAR",
        projection_key="def:LAR",
        name="LAR",
        position="DEF",
        team="LAR",
        selected_position="DEF",
        eligible_positions=["DEF"],
        points=3.0,
        n=1,
        source_points={"sleeper": 3.0},
    )
    ledger = _ledger([player], league_key=SLEEPER)
    by_code = fact_table(
        ledger,
        _bundle(
            [_actual("LAR", points=7.0, selected_position="DEF", name="LAR")],
            source="sleeper",
            league_key="1395854363380965376",
        ),
    )["facts"][0]
    by_canonical = fact_table(
        ledger,
        _bundle(
            [_actual("def:LAR", points=7.0, selected_position="DEF", name="LAR")],
            source="sleeper",
            league_key="1395854363380965376",
        ),
    )["facts"][0]
    assert by_code["native_id"] == "def:LAR"
    assert by_code["actual_points"] == 7.0
    assert by_canonical["actual_points"] == 7.0

    # A Sleeper skill id that happens to be Yahoo's Rams number stays a skill player.
    skill = _ledger(
        [_ledger_player(native_id="100014", position="WR", team="LAR", selected_position="WR")],
        league_key=SLEEPER,
    )
    table = fact_table(
        skill,
        _bundle(
            [
                _actual("100014", points=2.0, selected_position="WR"),
                _actual(
                    "def:LAR", points=20.0, selected_position="DEF", team_key="t.2", name="Rams"
                ),
            ],
            source="sleeper",
            league_key="1395854363380965376",
        ),
    )
    assert table["facts"][0]["native_id"] == "100014"
    assert table["facts"][0]["actual_points"] == 2.0
    assert table["facts"][0]["position"] == "WR"


def test_zero_skill_points_are_dnp_and_a_zero_defense_is_not():
    ledger = _ledger(
        [
            _ledger_player(points=9.0, source_points={"sleeper": 9.0}, n=1),
            _ledger_player(
                native_id="100014",
                player_key="def:LAR",
                projection_key="def:LAR",
                name="Rams",
                position="DEF",
                team="LAR",
                selected_position="DEF",
                eligible_positions=["DEF"],
                points=4.0,
                n=1,
                source_points={"sleeper": 4.0},
            ),
        ]
    )
    table = fact_table(
        ledger,
        _bundle(
            [
                _actual("4046", points=0.0),
                _actual("100014", points=0.0, selected_position="DEF", name="Rams"),
            ]
        ),
    )
    by_id = {row["native_id"]: row for row in table["facts"]}
    assert by_id["4046"]["dnp_proxy"] is True
    assert by_id["def:LAR"]["dnp_proxy"] is False


def test_advice_sets_recommended_slot_using_the_defense_alias():
    ledger = _ledger(
        [
            _ledger_player(
                native_id="100014",
                player_key="def:LAR",
                projection_key="def:LAR",
                name="Rams",
                position="DEF",
                team="LAR",
                selected_position="BN",
                eligible_positions=["DEF"],
                points=5.0,
                n=1,
                source_points={"espn": 5.0},
            ),
            _ledger_player(native_id="4046", selected_position="RB"),
        ]
    )
    advice = build_lineup_snapshot(
        season=2026,
        week=1,
        generated_at=STAMP,
        team_key="t.1",
        team_name="Brian",
        roster_slots={"DEF": 1, "RB": 1, "BN": 2},
        players=[
            {
                "native_id": "100014",
                "name": "Rams",
                "position": "DEF",
                "team": "LAR",
                "selected_position": "BN",
                "eligible_positions": ["DEF"],
                "projection_key": "def:LAR",
                "player_key": "def:LAR",
                "points": 5.0,
                "source_points": {"espn": 5.0},
                "n": 1,
                "matched": True,
            },
            {
                "native_id": "4046",
                "name": "Some Back",
                "position": "RB",
                "team": "KC",
                "selected_position": "RB",
                "eligible_positions": ["RB", "W/R/T"],
                "projection_key": "mfl:4046",
                "player_key": "mfl:4046",
                "points": 10.0,
                "source_points": {"sleeper": 12.0, "espn": 8.0},
                "n": 2,
                "matched": True,
            },
        ],
        report={
            "current": [],
            "optimal": [
                {
                    "native_id": "100014",
                    "name": "Rams",
                    "slot": "DEF",
                    "selected_position": "BN",
                    "points": 5.0,
                }
            ],
            "start": [],
            "sit": [],
            "current_total": 0,
            "optimal_total": 5,
            "delta": 5,
        },
    )
    table = fact_table(
        ledger,
        _bundle(
            [
                _actual("100014", points=9.0, selected_position="DEF", name="Rams"),
                _actual("4046", points=8.0),
            ]
        ),
        advice,
    )
    by_id = {row["native_id"]: row for row in table["facts"]}
    assert by_id["def:LAR"]["recommended_slot"] == "DEF"
    assert by_id["4046"]["recommended_slot"] == "BN"


def test_decision_table_is_user_team_starter_versus_eligible_bench():
    facts = [
        _fact(native_id="rb-start", consensus=10.0, actual_points=3.0, recommended_slot="RB"),
        _fact(
            native_id="rb-bench",
            name="Bench Back",
            consensus=8.0,
            actual_points=14.0,
            recommended_slot="BN",
            selected_position_at_advice="BN",
            actual_selected_position="BN",
        ),
        _fact(
            native_id="wr-bench",
            name="Bench Wide",
            position="WR",
            eligible_positions=["WR", "W/R/T"],
            consensus=9.0,
            actual_points=9.0,
            recommended_slot="BN",
            selected_position_at_advice="BN",
        ),
        _fact(
            native_id="out-bench",
            consensus=20.0,
            actual_points=20.0,
            recommended_slot="BN",
            injury_status="OUT",
        ),
        _fact(
            native_id="other-team",
            team_key="t.2",
            is_user_team=False,
            consensus=30.0,
            actual_points=30.0,
            recommended_slot=None,
        ),
        _fact(
            native_id="flex",
            position="WR",
            eligible_positions=["WR", "W/R/T"],
            consensus=7.0,
            actual_points=7.0,
            recommended_slot="W/R/T",
            selected_position_at_advice="W/R/T",
            actual_selected_position="W/R/T",
        ),
    ]

    rows = decision_table(facts)

    pairs = {(row["slot"], row["starter_native_id"], row["bench_native_id"]) for row in rows}
    assert pairs == {
        ("RB", "rb-start", "rb-bench"),
        ("W/R/T", "flex", "rb-bench"),
        ("W/R/T", "flex", "wr-bench"),
    }
    rb = next(row for row in rows if row["starter_native_id"] == "rb-start")
    assert rb["projected_gap"] == 2.0
    assert rb["realized_gap"] == -11.0
    assert rb["correct"] is False


def test_tied_gaps_follow_sign_equality():
    facts = [
        _fact(native_id="a", consensus=5.0, actual_points=5.0, recommended_slot="RB"),
        _fact(
            native_id="b",
            consensus=5.0,
            actual_points=5.0,
            recommended_slot="BN",
            selected_position_at_advice="BN",
        ),
    ]
    row = decision_table(facts)[0]
    assert row["projected_gap"] == 0.0
    assert row["realized_gap"] == 0.0
    assert row["correct"] is True


def test_history_uses_the_latest_ledger_and_reports_dnp_both_ways():
    early = _fact(
        generated_at=STAMP,
        consensus=1.0,
        actual_points=10.0,
        residual=9.0,
        source_points={"sleeper": 1.0},
        source_residuals={"sleeper": 9.0},
        recommended_slot="RB",
    )
    late_hit = _fact(
        generated_at=LATER,
        consensus=10.0,
        actual_points=10.0,
        residual=0.0,
        source_points={"sleeper": 10.0},
        source_residuals={"sleeper": 0.0},
        recommended_slot="RB",
        injury_status="QUESTIONABLE",
    )
    late_dnp = _fact(
        native_id="scratch",
        name="Scratch",
        generated_at=LATER,
        consensus=8.0,
        actual_points=0.0,
        residual=-8.0,
        source_points={"sleeper": 8.0},
        source_residuals={"sleeper": -8.0},
        dnp_proxy=True,
        recommended_slot="BN",
        selected_position_at_advice="BN",
        actual_selected_position="BN",
        position="RB",
    )
    late_qb = _fact(
        native_id="qb",
        name="QB",
        position="QB",
        eligible_positions=["QB"],
        generated_at=LATER,
        consensus=18.0,
        actual_points=16.0,
        residual=-2.0,
        source_points={"espn": 18.0},
        source_residuals={"espn": -2.0},
        recommended_slot="QB",
        selected_position_at_advice="QB",
        actual_selected_position="QB",
    )
    report = history_report(
        [early, late_hit, late_dnp, late_qb],
        season=2026,
        roster_slots={(YAHOO, 1): {"QB": 1, "RB": 1, "BN": 2}},
    )

    by_key = {(row["source"], row["position"]): row for row in report["source_position_accuracy"]}
    # Latest run only. Bias is projected − actual, the weekly retro sign.
    assert by_key[("sleeper", "RB")]["n"] == 2
    assert by_key[("sleeper", "RB")]["mae"] == 4.0  # (|10-10| + |8-0|) / 2
    assert by_key[("sleeper", "RB")]["bias"] == 4.0  # (0 + 8) / 2
    assert by_key[("sleeper", "RB")]["n_without_dnp"] == 1
    assert by_key[("sleeper", "RB")]["mae_without_dnp"] == 0.0
    assert by_key[("sleeper", "RB")]["bias_without_dnp"] == 0.0
    assert by_key[("espn", "QB")]["n"] == 1
    assert by_key[("espn", "QB")]["mae"] == 2.0
    assert by_key[("espn", "QB")]["bias"] == 2.0

    q = report["q_player_hit_rate"]
    # Q starter (late_hit, actual 10) vs scratch bench (actual 0): projected 10-8 > 0, realized > 0.
    assert q["n"] == 1
    assert q["hits"] == 1
    assert q["hit_rate"] == 1.0
    assert q["n_without_dnp"] == 0
    assert q["hit_rate_without_dnp"] is None

    decisions = report["decision_accuracy"]
    assert decisions["n"] == 1
    assert decisions["n_without_dnp"] == 0

    assert report["regret_trend"] == [
        {
            "league_key": YAHOO,
            "season": 2026,
            "week": 1,
            "generated_at": LATER,
            "recommended_total": 26.0,  # QB 16 + RB 10; scratch is benched
            "hindsight_total": 26.0,
            "regret": 0.0,
        }
    ]


def test_history_regret_matches_retro_hindsight_minus_recommended():
    """Regret is hindsight actuals minus the recommended lineup's actuals."""
    players = [
        _ledger_player(
            native_id="slow",
            name="Slow",
            player_key="slow",
            projection_key="slow",
            points=18.0,
            n=1,
            source_points={"sleeper": 18.0},
            selected_position="RB",
        ),
        _ledger_player(
            native_id="henry",
            name="Henry",
            player_key="henry",
            projection_key="henry",
            points=4.0,
            n=1,
            source_points={"sleeper": 4.0},
            selected_position="BN",
            eligible_positions=["RB", "W/R/T"],
        ),
    ]
    ledger = _ledger(players)
    actuals = _bundle(
        [
            _actual("slow", points=2.0, name="Slow"),
            _actual("henry", points=24.0, selected_position="BN", name="Henry"),
        ]
    )
    advice = build_lineup_snapshot(
        season=2026,
        week=1,
        generated_at=STAMP,
        team_key="t.1",
        team_name="Brian",
        roster_slots={"RB": 1, "BN": 2},
        players=[
            {
                "native_id": "slow",
                "name": "Slow",
                "position": "RB",
                "team": "KC",
                "selected_position": "RB",
                "eligible_positions": ["RB", "W/R/T"],
                "projection_key": "slow",
                "player_key": "slow",
                "points": 18.0,
                "source_points": {"sleeper": 18.0},
                "n": 1,
                "matched": True,
            },
            {
                "native_id": "henry",
                "name": "Henry",
                "position": "RB",
                "team": "KC",
                "selected_position": "BN",
                "eligible_positions": ["RB", "W/R/T"],
                "projection_key": "henry",
                "player_key": "henry",
                "points": 4.0,
                "source_points": {"sleeper": 4.0},
                "n": 1,
                "matched": True,
            },
        ],
        report={
            "current": [
                {
                    "native_id": "slow",
                    "name": "Slow",
                    "slot": "RB",
                    "selected_position": "RB",
                    "points": 18.0,
                }
            ],
            "optimal": [
                {
                    "native_id": "slow",
                    "name": "Slow",
                    "slot": "RB",
                    "selected_position": "RB",
                    "points": 18.0,
                }
            ],
            "start": [],
            "sit": [],
            "current_total": 18.0,
            "optimal_total": 18.0,
            "delta": 0.0,
        },
    )
    facts = fact_table(ledger, actuals, advice)["facts"]
    report = history_report(facts, season=2026, roster_slots={(YAHOO, 1): {"RB": 1, "BN": 2}})
    retro = retro_report(advice, actuals)
    regret = report["regret_trend"][0]
    assert regret["recommended_total"] == retro["recommended_total"]
    assert regret["hindsight_total"] == retro["hindsight_total"]
    assert regret["regret"] == round(retro["hindsight_total"] - retro["recommended_total"], 2)
    assert regret["regret"] == 22.0
    # Weekly retro source_accuracy is a different report and stays on its own contract.
    assert {row["source"] for row in retro["source_accuracy"]} == {"sleeper"}
    assert retro["source_accuracy"][0]["bias"] == pytest.approx(((18.0 - 2.0) + (4.0 - 24.0)) / 2)


def test_scoring_uses_the_newest_roster_and_each_players_pre_kickoff_row():
    """Post-kickoff ledgers must not label Thursday players, and dropped players stay dropped."""
    wednesday = "2026-10-07T11:00:00Z"
    sunday_run = "2026-10-11T15:00:00Z"
    kickoffs = {(1, "PHI"): "2026-10-08T00:20:00Z", (1, "KC"): "2026-10-11T17:00:00Z"}
    thursday_early = _fact(
        native_id="thu",
        name="Thursday",
        nfl_team="PHI",
        generated_at=wednesday,
        consensus=1.0,
        actual_points=20.0,
        source_points={"sleeper": 1.0},
    )
    thursday_late = _fact(
        native_id="thu",
        name="Thursday",
        nfl_team="PHI",
        generated_at=sunday_run,
        consensus=20.0,
        actual_points=20.0,
        source_points={"sleeper": 20.0},
    )
    sunday_early = _fact(
        native_id="sun",
        nfl_team="KC",
        generated_at=wednesday,
        consensus=3.0,
        actual_points=10.0,
        source_points={"sleeper": 3.0},
    )
    sunday_late = _fact(
        native_id="sun",
        nfl_team="KC",
        generated_at=sunday_run,
        consensus=9.0,
        actual_points=10.0,
        source_points={"sleeper": 9.0},
    )
    dropped = _fact(
        native_id="gone",
        nfl_team="PHI",
        generated_at=wednesday,
        consensus=50.0,
        actual_points=0.0,
        source_points={"sleeper": 50.0},
    )
    only_after = _fact(
        native_id="after",
        nfl_team="PHI",
        generated_at=sunday_run,
        consensus=7.0,
        actual_points=7.0,
        source_points={"sleeper": 7.0},
    )
    no_kickoff = _fact(
        native_id="bye",
        nfl_team="DAL",
        generated_at=sunday_run,
        consensus=4.0,
        actual_points=4.0,
        source_points={"sleeper": 4.0},
    )

    chosen = select_scoring_facts(
        [thursday_early, thursday_late, sunday_early, sunday_late, dropped, only_after, no_kickoff],
        kickoffs,
    )
    by_id = {row["native_id"]: row for row in chosen}

    assert set(by_id) == {"thu", "sun"}
    assert by_id["thu"]["generated_at"] == wednesday
    assert by_id["thu"]["consensus"] == 1.0
    assert by_id["thu"]["hours_before_kickoff"] == 13.33
    assert by_id["sun"]["generated_at"] == sunday_run
    assert by_id["sun"]["consensus"] == 9.0
    assert by_id["thu"]["roster_generated_at"] == sunday_run
    report = history_report(
        [thursday_early, thursday_late, sunday_early, sunday_late, dropped, only_after, no_kickoff],
        season=2026,
        kickoffs=kickoffs,
    )
    sleeper = next(row for row in report["source_position_accuracy"] if row["source"] == "sleeper")
    assert sleeper["n"] == 2
    assert sleeper["mae"] == 10.0  # |1-20| and |9-10|
    assert sleeper["bias"] == -10.0
    assert report["regret_trend"] == []
    assert any("do not share the advice ledger" in note for note in report["notes"])


def test_a_prediction_at_kickoff_is_not_scored():
    kickoff = "2026-10-11T17:00:00Z"
    rows = select_scoring_facts(
        [_fact(generated_at=kickoff, nfl_team="KC")],
        {(1, "KCC"): kickoff},
    )

    assert rows == []


def test_missing_kickoff_times_score_nothing():
    report = history_report([_fact()], season=2026, kickoffs={})

    assert report["source_position_accuracy"] == []
    assert report["regret_trend"] == []
    assert report["notes"] == [NOTE_NO_KICKOFFS]


def test_decision_metrics_require_an_exact_advice_timestamp():
    report = history_report([_fact(recommended_slot=None)], season=2026, roster_slots={})

    assert report["source_position_accuracy"]
    assert report["regret_trend"] == []
    assert report["decision_accuracy"]["n"] == 0
    assert report["q_player_hit_rate"]["n"] == 0
    assert any(NOTE_UNPAIRED in note for note in report["notes"])
    assert paired_advice([{"generated_at": STAMP}], LATER) is None
    assert (
        paired_advice([{"generated_at": STAMP}, {"generated_at": LATER}], LATER)["generated_at"]
        == LATER
    )


def test_regret_skips_weeks_with_no_joined_actuals():
    empty = _fact(week=1, actual_points=None, residual=None, recommended_slot="RB")
    scored = _fact(week=2, native_id="w2", actual_points=8.0, consensus=10.0, recommended_slot="RB")

    report = history_report(
        [empty, scored],
        season=2026,
        roster_slots={(YAHOO, 1): {"RB": 1, "BN": 1}, (YAHOO, 2): {"RB": 1, "BN": 1}},
    )

    assert [row["week"] for row in report["regret_trend"]] == [2]
    assert report["regret_trend"][0]["regret"] == 0.0
    assert report["weeks_without_actuals"] == [{"league_key": YAHOO, "week": 1}]


def test_fact_table_rejects_an_actuals_league_mismatch():
    ledger = _ledger([_ledger_player()])
    with pytest.raises(ValueError, match="league"):
        fact_table(
            ledger,
            _bundle([_actual("4046", points=1.0)], source="sleeper", league_key="999"),
        )
