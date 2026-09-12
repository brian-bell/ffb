"""Sit/start optimizer: weekly points vs stored selected_position."""

from ffb.lineup import (
    CLOSE_CALL_POINTS,
    attach_weekly_points,
    compare_lineup,
    projection_key,
)


def _roster(**changes):
    row = {
        "yahoo_player_id": "1",
        "full_name": "Player",
        "nfl_team": "BAL",
        "primary_position": "RB",
        "eligible_positions": ["RB", "W/R/T"],
        "selected_position": "BN",
        "player_key": "yahoo:1",
        "matched": False,
    }
    row.update(changes)
    return row


def _consensus(player_key, points, n=1):
    return {
        "player_key": player_key,
        "full_name": "Consensus",
        "position": "RB",
        "team": "BAL",
        "matched": True,
        "consensus": points,
        "n": n,
        "source_points": {"sleeper": points},
    }


def test_projection_key_uses_canonical_key_for_matched_skill_players():
    assert projection_key(_roster(matched=True, player_key="12626")) == "12626"


def test_projection_key_joins_unmatched_defenses_by_nfl_team():
    row = _roster(
        full_name="Rams",
        nfl_team="LAR",
        primary_position="DEF",
        eligible_positions=["DEF"],
        selected_position="DEF",
        player_key="yahoo:100014",
        matched=False,
    )
    assert projection_key(row) == "def:LAR"


def test_projection_key_does_not_guess_unmatched_skill_players():
    assert projection_key(_roster(full_name="Kyle Monangai", yahoo_player_id="42025")) is None


def test_optimal_starts_higher_weekly_rb_over_current_starter():
    players = attach_weekly_points(
        [
            _roster(
                yahoo_player_id="55501",
                full_name="Slow Back",
                primary_position="RB",
                selected_position="RB",
                player_key="rb-low",
                matched=True,
            ),
            _roster(
                yahoo_player_id="29279",
                full_name="Derrick Henry",
                selected_position="BN",
                player_key="12626",
                matched=True,
            ),
        ],
        [_consensus("rb-low", 4.0), _consensus("12626", 18.0)],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 2})

    assert report["current_total"] == 4.0
    assert report["optimal_total"] == 18.0
    assert report["delta"] == 14.0
    assert [row["name"] for row in report["current"]] == ["Slow Back"]
    assert [row["name"] for row in report["optimal"]] == ["Derrick Henry"]
    assert [row["name"] for row in report["start"]] == ["Derrick Henry"]
    assert [row["name"] for row in report["sit"]] == ["Slow Back"]


def test_restrictive_flex_is_claimed_before_broader_flex():
    players = attach_weekly_points(
        [
            _roster(
                yahoo_player_id="w1",
                full_name="Top WR",
                primary_position="WR",
                eligible_positions=["WR", "W/T", "W/R/T"],
                selected_position="BN",
                player_key="w1",
                matched=True,
            ),
            _roster(
                yahoo_player_id="r1",
                full_name="Top RB",
                primary_position="RB",
                selected_position="BN",
                player_key="r1",
                matched=True,
            ),
            _roster(
                yahoo_player_id="t1",
                full_name="Backup TE",
                nfl_team="DET",
                primary_position="TE",
                eligible_positions=["TE", "W/T", "W/R/T"],
                selected_position="TE",
                player_key="t1",
                matched=True,
            ),
        ],
        [_consensus("w1", 20.0), _consensus("r1", 18.0), _consensus("t1", 8.0)],
    )
    report = compare_lineup(players, {"TE": 1, "W/T": 1, "W/R/T": 1, "BN": 2})
    by_slot = {row["slot"]: row["name"] for row in report["optimal"]}

    assert by_slot == {"TE": "Backup TE", "W/T": "Top WR", "W/R/T": "Top RB"}


def test_unmatched_defense_receives_weekly_points_via_team_key():
    players = attach_weekly_points(
        [
            _roster(
                yahoo_player_id="100025",
                full_name="49ers",
                nfl_team="SFO",
                primary_position="DEF",
                eligible_positions=["DEF"],
                selected_position="DEF",
                player_key="yahoo:100025",
                matched=False,
            )
        ],
        [_consensus("def:SFO", 7.5)],
    )

    assert players[0]["projection_key"] == "def:SFO"
    assert players[0]["points"] == 7.5
    assert compare_lineup(players, {"DEF": 1})["optimal_total"] == 7.5


def test_missing_weekly_points_are_excluded_from_optimal_and_listed():
    players = attach_weekly_points(
        [
            _roster(
                yahoo_player_id="42025",
                full_name="Rookie Bench",
                selected_position="RB",
                player_key="yahoo:42025",
                matched=False,
            ),
            _roster(
                yahoo_player_id="29279",
                full_name="Derrick Henry",
                selected_position="BN",
                player_key="12626",
                matched=True,
            ),
        ],
        [_consensus("12626", 18.0)],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 1})

    assert [row["name"] for row in report["missing_projections"]] == ["Rookie Bench"]
    assert [row["name"] for row in report["optimal"]] == ["Derrick Henry"]
    assert report["current_total"] == 0.0


def test_close_call_flags_bench_within_threshold_of_a_starter_slot():
    players = attach_weekly_points(
        [
            _roster(
                yahoo_player_id="start",
                full_name="Starter RB",
                selected_position="RB",
                player_key="start",
                matched=True,
            ),
            _roster(
                yahoo_player_id="bench",
                full_name="Close RB",
                selected_position="BN",
                player_key="bench",
                matched=True,
            ),
        ],
        [_consensus("start", 12.0), _consensus("bench", 12.0 - CLOSE_CALL_POINTS)],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 1})

    assert report["start"] == []
    assert len(report["close_calls"]) == 1
    assert report["close_calls"][0]["name"] == "Close RB"
    assert report["close_calls"][0]["versus"] == "Starter RB"
    assert report["close_calls"][0]["delta"] == CLOSE_CALL_POINTS
