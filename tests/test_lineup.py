"""Sit/start optimizer: weekly points vs stored selected_position."""

import pytest

from ffb.lineup import (
    CLOSE_CALL_POINTS,
    align_lineup_slots,
    attach_injuries,
    attach_weekly_points,
    compare_lineup,
    injury_badge,
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
    assert [row["name"] for row in report["sit"]] == ["Rookie Bench"]
    assert report["current_total"] == 0.0


def test_unprojected_starter_is_not_sat_when_no_replacement_is_assigned():
    players = attach_weekly_points(
        [
            _roster(
                yahoo_player_id="42025",
                full_name="Rookie QB",
                primary_position="QB",
                eligible_positions=["QB"],
                selected_position="QB",
                player_key="yahoo:42025",
                matched=False,
            )
        ],
        [],
    )
    report = compare_lineup(players, {"QB": 1, "RB": 1})

    assert report["sit"] == []
    assert report["start"] == []
    assert [row["name"] for row in report["undecidable"]] == ["Rookie QB"]
    qb = next(row for row in report["aligned"] if row["slot"] == "QB")
    assert qb["current"]["name"] == "Rookie QB"
    assert qb["optimal"]["name"] == "Rookie QB"
    rb = next(row for row in report["aligned"] if row["slot"] == "RB")
    assert rb["current"] is None
    assert rb["optimal"] is None


def test_aligned_rows_pair_by_slot_when_optimal_qb_is_vacant():
    players = attach_weekly_points(
        [
            _roster(
                yahoo_player_id="qb1",
                full_name="Unprojected QB",
                primary_position="QB",
                eligible_positions=["QB"],
                selected_position="QB",
                player_key="yahoo:qb1",
                matched=False,
            ),
            _roster(
                yahoo_player_id="rb1",
                full_name="Projected RB",
                selected_position="RB",
                player_key="rb1",
                matched=True,
            ),
        ],
        [_consensus("rb1", 12.0)],
    )
    report = compare_lineup(players, {"QB": 1, "RB": 1})
    aligned = align_lineup_slots(report["current"], report["optimal"], {"QB": 1, "RB": 1})

    assert [row["slot"] for row in aligned] == ["QB", "RB"]
    assert aligned[0]["current"]["name"] == "Unprojected QB"
    assert aligned[0]["optimal"] is None or aligned[0]["optimal"]["name"] == "Unprojected QB"
    assert aligned[1]["current"]["name"] == "Projected RB"
    assert aligned[1]["optimal"]["name"] == "Projected RB"
    assert aligned[1]["optimal"]["name"] != aligned[0]["current"]["name"]


def test_equal_weekly_points_keep_the_current_starter():
    players = attach_weekly_points(
        [
            _roster(
                yahoo_player_id="100014",
                full_name="Rams",
                nfl_team="LAR",
                primary_position="DEF",
                eligible_positions=["DEF"],
                selected_position="DEF",
                player_key="yahoo:100014",
                matched=False,
            ),
            _roster(
                yahoo_player_id="100033",
                full_name="Ravens",
                nfl_team="BAL",
                primary_position="DEF",
                eligible_positions=["DEF"],
                selected_position="BN",
                player_key="yahoo:100033",
                matched=False,
            ),
        ],
        [_consensus("def:LAR", 7.04), _consensus("def:BAL", 7.01)],
    )
    report = compare_lineup(players, {"DEF": 1, "BN": 1})

    assert [row["name"] for row in report["optimal"]] == ["Rams"]
    assert report["start"] == []
    assert report["sit"] == []


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


def _injury(player_key, status, *, matched=True, fetched_at="2026-09-12T12:00:00Z"):
    return {
        "player_key": player_key,
        "season": 2024,
        "source": "sleeper",
        "native_id": player_key,
        "status": status,
        "fetched_at": fetched_at,
        "matched": matched,
    }


def test_attach_injuries_joins_matched_status_onto_current_and_optimal_rows():
    players = attach_injuries(
        attach_weekly_points(
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
                    full_name="Bench RB",
                    selected_position="BN",
                    player_key="bench",
                    matched=True,
                ),
            ],
            [_consensus("start", 10.0), _consensus("bench", 8.0)],
        ),
        [_injury("start", "QUESTIONABLE"), _injury("bench", "OUT")],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 1})

    assert report["current"][0]["injury"] == {
        "status": "QUESTIONABLE",
        "fetched_at": "2026-09-12T12:00:00Z",
    }
    assert report["optimal"][0]["injury"]["status"] == "QUESTIONABLE"
    assert injury_badge(report["current"][0]) == "Q"
    assert report["injury_as_of"] == "2026-09-12T12:00:00Z"


def test_attach_injuries_ignores_unmatched_and_blank_status():
    players = attach_injuries(
        attach_weekly_points(
            [
                _roster(
                    full_name="Starter RB",
                    selected_position="RB",
                    player_key="start",
                    matched=True,
                )
            ],
            [_consensus("start", 10.0)],
        ),
        [
            _injury("start", "OUT", matched=False),
            _injury("other", None),
        ],
    )

    assert players[0]["injury"] is None
    assert compare_lineup(players, {"RB": 1})["optimal"][0]["name"] == "Starter RB"


def test_out_starter_is_sat_for_healthy_backup():
    players = attach_injuries(
        attach_weekly_points(
            [
                _roster(
                    yahoo_player_id="out",
                    full_name="Out Starter",
                    selected_position="RB",
                    player_key="out",
                    matched=True,
                ),
                _roster(
                    yahoo_player_id="backup",
                    full_name="Healthy Backup",
                    selected_position="BN",
                    player_key="backup",
                    matched=True,
                ),
            ],
            [_consensus("out", 22.0), _consensus("backup", 6.0)],
        ),
        [_injury("out", "OUT")],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 1})

    assert [row["name"] for row in report["optimal"]] == ["Healthy Backup"]
    assert [row["name"] for row in report["start"]] == ["Healthy Backup"]
    assert [row["name"] for row in report["sit"]] == ["Out Starter"]
    assert report["sit"][0]["injury"]["status"] == "OUT"
    assert report["start"][0].get("injury") is None


@pytest.mark.parametrize("status", ["DOUBTFUL", "IR", "PUP", "NFI"])
def test_unavailable_status_is_not_started(status):
    players = attach_injuries(
        attach_weekly_points(
            [
                _roster(
                    yahoo_player_id="down",
                    full_name="Unavailable Star",
                    selected_position="BN",
                    player_key="down",
                    matched=True,
                ),
                _roster(
                    yahoo_player_id="ok",
                    full_name="Healthy Starter",
                    selected_position="RB",
                    player_key="ok",
                    matched=True,
                ),
            ],
            [_consensus("down", 20.0), _consensus("ok", 5.0)],
        ),
        [_injury("down", status)],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 1})

    assert [row["name"] for row in report["optimal"]] == ["Healthy Starter"]
    assert report["start"] == []
    assert report["sit"] == []


def test_questionable_player_can_still_be_the_optimal_starter():
    players = attach_injuries(
        attach_weekly_points(
            [
                _roster(
                    yahoo_player_id="q",
                    full_name="Questionable Star",
                    selected_position="BN",
                    player_key="q",
                    matched=True,
                ),
                _roster(
                    yahoo_player_id="ok",
                    full_name="Healthy Backup",
                    selected_position="RB",
                    player_key="ok",
                    matched=True,
                ),
            ],
            [_consensus("q", 18.0), _consensus("ok", 8.0)],
        ),
        [_injury("q", "QUESTIONABLE")],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 1})

    assert [row["name"] for row in report["optimal"]] == ["Questionable Star"]
    assert [row["name"] for row in report["start"]] == ["Questionable Star"]
    assert report["optimal"][0]["injury"]["status"] == "QUESTIONABLE"


def test_out_starter_is_not_retained_when_the_slot_has_no_replacement():
    players = attach_injuries(
        attach_weekly_points(
            [
                _roster(
                    yahoo_player_id="out",
                    full_name="Out QB",
                    primary_position="QB",
                    eligible_positions=["QB"],
                    selected_position="QB",
                    player_key="out",
                    matched=True,
                )
            ],
            [_consensus("out", 16.0)],
        ),
        [_injury("out", "OUT")],
    )
    report = compare_lineup(players, {"QB": 1})

    assert [row["name"] for row in report["current"]] == ["Out QB"]
    assert report["optimal"] == []
    assert [row["name"] for row in report["sit"]] == ["Out QB"]
    assert report["undecidable"] == []
    qb = next(row for row in report["aligned"] if row["slot"] == "QB")
    assert qb["current"]["name"] == "Out QB"
    assert qb["optimal"] is None


def test_unavailable_bench_is_not_a_close_call():
    players = attach_injuries(
        attach_weekly_points(
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
                    full_name="Out Close RB",
                    selected_position="BN",
                    player_key="bench",
                    matched=True,
                ),
            ],
            [_consensus("start", 12.0), _consensus("bench", 12.0 - CLOSE_CALL_POINTS)],
        ),
        [_injury("bench", "OUT")],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 1})

    assert report["close_calls"] == []
    assert report["start"] == []
