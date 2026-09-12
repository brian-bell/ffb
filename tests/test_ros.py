"""Pure rest-of-season strategy compute: consensus, playoff slate, bye plan."""

from __future__ import annotations

import pytest

from ffb import ros


def _consensus(player_key, name, position, team, points, *, n=1):
    return {
        "player_key": player_key,
        "full_name": name,
        "position": position,
        "team": team,
        "matched": True,
        "source_points": {"sleeper": points},
        "consensus": points,
        "n": n,
        "rank": 0,
        "draftable": True,
    }


def _game(week, home, away, season=2026):
    return {
        "season": season,
        "source": "schedule",
        "week": week,
        "home_team": home,
        "away_team": away,
    }


def _bye(team, week, season=2026):
    return {"season": season, "source": "schedule", "team": team, "bye": week}


def _roster(name, team, position, selected, *, player_key="k"):
    return {
        "player_key": player_key,
        "full_name": name,
        "name": name,
        "nfl_team": team,
        "primary_position": position,
        "selected_position": selected,
        "matched": True,
    }


def test_parse_playoff_weeks_defaults_and_splits_csv():
    assert ros.parse_playoff_weeks(None) == ros.PLAYOFF_WEEKS
    assert ros.parse_playoff_weeks("  ") == ros.PLAYOFF_WEEKS
    assert ros.parse_playoff_weeks("14,15,16") == (14, 15, 16)


def test_parse_playoff_weeks_rejects_invalid_values():
    with pytest.raises(ValueError, match="positive"):
        ros.parse_playoff_weeks("15,0")
    with pytest.raises(ValueError, match="positive"):
        ros.parse_playoff_weeks("15,x")


def test_playoff_slate_lists_home_and_away_opponents_in_week_order():
    slate = ros.playoff_slate(
        [_game(16, "KCC", "BAL"), _game(15, "BAL", "BUF"), _game(17, "CIN", "BAL")],
        weeks=(15, 16, 17),
        byes=[_bye("BAL", 14)],
    )
    bal = slate["BAL"]
    assert [(row["week"], row["opponent"], row["home"]) for row in bal] == [
        (15, "BUF", True),
        (16, "KCC", False),
        (17, "CIN", False),
    ]


def test_playoff_slate_inserts_bye_when_it_falls_in_playoff_weeks():
    slate = ros.playoff_slate(
        [_game(15, "BAL", "BUF"), _game(17, "BAL", "CIN")],
        weeks=(15, 16, 17),
        byes=[_bye("BAL", 16)],
    )
    assert [(row["week"], row["opponent"], row["bye"]) for row in slate["BAL"]] == [
        (15, "BUF", False),
        (16, None, True),
        (17, "CIN", False),
    ]


def test_playoff_slate_skips_weeks_without_a_game_or_bye():
    slate = ros.playoff_slate([_game(15, "BAL", "BUF")], weeks=(15, 16, 17))
    assert [row["week"] for row in slate["BAL"]] == [15]


def test_format_matchups_marks_road_games_and_byes():
    label = ros.format_matchups(
        [
            {"week": 15, "opponent": "BUF", "home": True, "bye": False},
            {"week": 16, "opponent": None, "home": False, "bye": True},
            {"week": 17, "opponent": "KCC", "home": False, "bye": False},
        ]
    )
    assert label == "BUF, BYE, @KCC"


def test_playoff_strength_ranks_harder_opponent_defenses_first():
    slate = ros.playoff_slate(
        [_game(15, "BAL", "BUF"), _game(15, "CIN", "CLE")],
        weeks=(15,),
    )
    strength = ros.playoff_strength(slate, {"BUF": 12.0, "CLE": 3.0, "BAL": 8.0, "CIN": 5.0})
    assert strength["BAL"]["avg_opp_def"] == 12.0
    assert strength["CIN"]["avg_opp_def"] == 3.0
    assert strength["BAL"]["difficulty"] == 1
    assert strength["CIN"]["difficulty"] > strength["BAL"]["difficulty"]
    assert strength["BAL"]["games"] == 1


def test_playoff_strength_ignores_bye_weeks_in_the_average():
    slate = ros.playoff_slate(
        [_game(15, "BAL", "BUF")],
        weeks=(15, 16),
        byes=[_bye("BAL", 16)],
    )
    strength = ros.playoff_strength(slate, {"BUF": 10.0})
    assert strength["BAL"]["avg_opp_def"] == 10.0
    assert strength["BAL"]["games"] == 1


def test_ros_rows_join_season_consensus_to_bye_and_playoff_slate():
    report = ros.ros_report(
        [_consensus("12626", "Derrick Henry", "RB", "BAL", 180.0)],
        byes=[_bye("BAL", 14)],
        games=[_game(15, "BAL", "BUF"), _game(16, "KCC", "BAL")],
        playoff_weeks=(15, 16),
        def_points={"BUF": 12.0, "KCC": 4.0},
    )
    row = report["players"][0]
    assert row["name"] == "Derrick Henry"
    assert row["ros"] == 180.0
    assert row["bye"] == 14
    assert row["playoff"] == "BUF, @KCC"
    assert row["playoff_difficulty"] == 1
    assert row["avg_opp_def"] == 8.0


def test_ros_report_filters_by_position_and_keeps_rank_order():
    report = ros.ros_report(
        [
            _consensus("1", "Alpha WR", "WR", "CIN", 200.0),
            _consensus("2", "Beta RB", "RB", "BAL", 150.0),
            _consensus("3", "Gamma WR", "WR", "CHI", 100.0),
        ],
        byes=[],
        games=[],
        playoff_weeks=(15, 16, 17),
        position="WR",
    )
    assert [row["name"] for row in report["players"]] == ["Alpha WR", "Gamma WR"]
    assert [row["rank"] for row in report["players"]] == [1, 2]


def test_bye_plan_flags_two_starters_at_the_same_position():
    plan = ros.bye_plan(
        [
            _roster("Derrick Henry", "BAL", "RB", "RB", player_key="henry"),
            _roster("Slow Back", "BAL", "RB", "W/R/T", player_key="slow"),
            _roster("Ja'Marr Chase", "CIN", "WR", "WR", player_key="chase"),
        ],
        [_bye("BAL", 14), _bye("CIN", 12)],
    )
    week14 = next(group for group in plan if group["bye"] == 14)
    assert week14["thin_positions"] == ["RB"]
    assert [player["name"] for player in week14["players"]] == ["Derrick Henry", "Slow Back"]
    week12 = next(group for group in plan if group["bye"] == 12)
    assert week12["thin_positions"] == []


def test_bye_plan_does_not_treat_bench_as_a_thin_starter():
    plan = ros.bye_plan(
        [
            _roster("Derrick Henry", "BAL", "RB", "RB", player_key="henry"),
            _roster("Backup", "BAL", "RB", "BN", player_key="backup"),
        ],
        [_bye("BAL", 14)],
    )
    assert plan[0]["thin_positions"] == []
    assert len(plan[0]["players"]) == 2


def test_bye_plan_omits_weeks_before_current_week():
    plan = ros.bye_plan(
        [
            _roster("Slow Back", "KCC", "RB", "RB", player_key="slow"),
            _roster("Ja'Marr Chase", "CIN", "WR", "WR", player_key="chase"),
            _roster("Derrick Henry", "BAL", "RB", "IR", player_key="henry"),
        ],
        [_bye("KCC", 5), _bye("CIN", 12), _bye("BAL", 14)],
        current_week=10,
    )
    assert [group["bye"] for group in plan] == [12, 14]
    henry = next(player for player in plan[-1]["players"] if player["name"] == "Derrick Henry")
    assert henry["selected_position"] == "IR"
    assert henry["starter"] is False


def test_ros_report_omits_completed_playoff_weeks():
    report = ros.ros_report(
        [_consensus("12626", "Derrick Henry", "RB", "BAL", 180.0)],
        byes=[_bye("BAL", 14)],
        games=[_game(15, "BAL", "BUF"), _game(16, "KCC", "BAL"), _game(17, "BAL", "CIN")],
        playoff_weeks=(15, 16, 17),
        def_points={"BUF": 12.0, "KCC": 4.0, "CIN": 6.0},
        current_week=16,
        roster=[_roster("Derrick Henry", "BAL", "RB", "RB")],
    )
    assert report["playoff_weeks"] == (16, 17)
    assert report["players"][0]["playoff"] == "@KCC, CIN"
    assert report["players"][0]["avg_opp_def"] == 5.0
    assert report["bye_plan"] == []


def test_ros_report_includes_team_strength_and_optional_bye_plan():
    report = ros.ros_report(
        [_consensus("12626", "Derrick Henry", "RB", "BAL", 180.0)],
        byes=[_bye("BAL", 14)],
        games=[_game(15, "BAL", "BUF")],
        playoff_weeks=(15,),
        def_points={"BUF": 9.0},
        roster=[_roster("Derrick Henry", "BAL", "RB", "RB")],
    )
    assert report["playoff_weeks"] == (15,)
    assert report["teams"][0]["team"] == "BAL"
    assert report["bye_plan"][0]["bye"] == 14
    assert report["usage_available"] is False
