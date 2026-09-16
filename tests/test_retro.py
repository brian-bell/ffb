"""Weekly retro: sit/start advice vs locked actuals."""

import pytest

from ffb.lineup import attach_weekly_points, compare_lineup
from ffb.retro import (
    actuals_snapshot_key,
    build_lineup_snapshot,
    lineup_snapshot_key,
    parse_lineup_snapshot,
    retro_report,
)


def _player(**changes):
    row = {
        "yahoo_player_id": "1",
        "yahoo_player_key": "1.p.1",
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


def _consensus(player_key, points, source_points=None, n=None):
    sources = source_points or {"sleeper": points}
    return {
        "player_key": player_key,
        "full_name": "Consensus",
        "position": "RB",
        "team": "BAL",
        "matched": True,
        "consensus": points,
        "n": n if n is not None else len(sources),
        "source_points": sources,
    }


def _advice():
    players = attach_weekly_points(
        [
            _player(
                yahoo_player_id="55501",
                yahoo_player_key="1.p.55501",
                full_name="Slow Back",
                primary_position="RB",
                selected_position="RB",
                player_key="rb-low",
                matched=True,
            ),
            _player(
                yahoo_player_id="29279",
                yahoo_player_key="1.p.29279",
                full_name="Derrick Henry",
                selected_position="BN",
                player_key="12626",
                matched=True,
            ),
        ],
        [
            _consensus("rb-low", 4.0, {"sleeper": 5.0, "espn": 3.0}),
            _consensus("12626", 18.0, {"sleeper": 19.0, "espn": 17.0}),
        ],
    )
    report = compare_lineup(players, {"RB": 1, "BN": 2})
    return build_lineup_snapshot(
        season=2024,
        week=1,
        generated_at="2026-09-13T13:00:00Z",
        team_key="1.l.sit.t.1",
        team_name="Brian's Team",
        roster_slots={"RB": 1, "BN": 2},
        players=players,
        report=report,
    )


def _actuals(*, henry_slot="BN", henry_points=24.0, slow_slot="RB", slow_points=2.0):
    return {
        "schema_version": 1,
        "source": "fixture",
        "synced_at": "2026-09-16T16:00:00Z",
        "league": {
            "league_id": "sit-1",
            "league_key": "1.l.sit",
            "name": "Sit Start Mock",
            "season": 2024,
            "week": 1,
            "num_teams": 2,
        },
        "matchups": [
            {
                "matchup_id": "1",
                "week": 1,
                "teams": [
                    {"team_key": "1.l.sit.t.1", "points": henry_points + slow_points},
                    {"team_key": "1.l.sit.t.2", "points": 10.0},
                ],
            }
        ],
        "players": [
            {
                "yahoo_player_id": "55501",
                "yahoo_player_key": "1.p.55501",
                "name": "Slow Back",
                "team_key": "1.l.sit.t.1",
                "selected_position": slow_slot,
                "points": slow_points,
            },
            {
                "yahoo_player_id": "29279",
                "yahoo_player_key": "1.p.29279",
                "name": "Derrick Henry",
                "team_key": "1.l.sit.t.1",
                "selected_position": henry_slot,
                "points": henry_points,
            },
            {
                "yahoo_player_id": "99901",
                "yahoo_player_key": "1.p.99901",
                "name": "Rival Receiver",
                "team_key": "1.l.sit.t.2",
                "selected_position": "WR",
                "points": 10.0,
            },
        ],
    }


def test_snapshot_keys_are_week_scoped():
    assert lineup_snapshot_key(2024, 1) == "lineup/2024_week1"
    assert actuals_snapshot_key(2026, 12) == "actuals/2026_week12"


def test_lineup_snapshot_is_closed_and_round_trips():
    snapshot = parse_lineup_snapshot(_advice())
    assert snapshot["kind"] == "lineup_recommendation"
    assert snapshot["team_key"] == "1.l.sit.t.1"
    henry = next(row for row in snapshot["players"] if row["yahoo_player_id"] == "29279")
    assert henry["source_points"] == {"sleeper": 19.0, "espn": 17.0}
    assert henry["points"] == 18.0


def test_attach_weekly_points_copies_source_points():
    players = attach_weekly_points(
        [_player(yahoo_player_id="29279", player_key="12626", matched=True)],
        [_consensus("12626", 18.0, {"sleeper": 19.0, "espn": 17.0})],
    )
    assert players[0]["source_points"] == {"sleeper": 19.0, "espn": 17.0}


def test_retro_uses_locked_actuals_not_advice_time_lineup():
    retro = retro_report(_advice(), _actuals(henry_slot="RB", slow_slot="BN"))
    assert retro["recommended_total"] == 24.0
    assert retro["started_total"] == 24.0
    assert retro["delta"] == 0.0
    assert [row["name"] for row in retro["start_hits"]] == ["Derrick Henry"]
    assert [row["name"] for row in retro["sit_hits"]] == ["Slow Back"]
    assert retro["start_misses"] == []
    assert retro["sit_misses"] == []


def test_retro_scores_ignored_sit_start_advice_with_actual_points():
    retro = retro_report(_advice(), _actuals())
    assert retro["recommended_total"] == 24.0
    assert retro["started_total"] == 2.0
    assert retro["delta"] == 22.0
    assert retro["advice_lineup_total"] == 2.0
    assert [row["name"] for row in retro["start_misses"]] == ["Derrick Henry"]
    assert [row["name"] for row in retro["sit_misses"]] == ["Slow Back"]
    assert retro["matchup"]["user_points"] == 26.0
    assert retro["matchup"]["opponent_points"] == 10.0


def test_retro_reports_per_source_accuracy():
    retro = retro_report(_advice(), _actuals())
    by_source = {row["source"]: row for row in retro["source_accuracy"]}
    assert by_source["sleeper"]["n"] == 2
    assert by_source["sleeper"]["mae"] == 4.0  # (|19-24| + |5-2|) / 2
    assert by_source["sleeper"]["bias"] == -1.0  # (19-24 + 5-2) / 2
    assert by_source["espn"]["n"] == 2
    assert by_source["espn"]["mae"] == 4.0
    assert by_source["espn"]["bias"] == -3.0


def test_retro_lists_players_missing_actuals():
    actuals = _actuals()
    actuals["players"] = [row for row in actuals["players"] if row["yahoo_player_id"] != "29279"]
    retro = retro_report(_advice(), actuals)
    assert [row["name"] for row in retro["missing_actuals"]] == ["Derrick Henry"]
    assert retro["recommended_total"] == 0.0


def test_retro_rejects_actuals_that_omit_the_snapshot_team():
    actuals = _actuals()
    actuals["matchups"][0]["teams"] = [
        {"team_key": "1.l.other.t.1", "points": 10.0},
        {"team_key": "1.l.other.t.2", "points": 8.0},
    ]
    for row in actuals["players"]:
        row["team_key"] = "1.l.other.t.1"
    with pytest.raises(ValueError, match="team_key"):
        retro_report(_advice(), actuals)


def test_retro_does_not_treat_advice_as_started_when_team_has_no_player_rows():
    actuals = _actuals()
    for row in actuals["players"]:
        if row["team_key"] == "1.l.sit.t.1":
            row["team_key"] = "1.l.sit.t.2"
    retro = retro_report(_advice(), actuals)
    assert retro["started_total"] == 0.0
    assert retro["recommended_total"] == 24.0
    assert retro["start_misses"][0]["name"] == "Derrick Henry"


def test_retro_validates_dict_actuals_like_a_bundle():
    broken = _actuals()
    del broken["synced_at"]
    with pytest.raises(ValueError, match="unknown or missing"):
        retro_report(_advice(), broken)


def test_retro_rejects_dict_actuals_for_another_season():
    other = _actuals()
    other["league"]["season"] = 2023
    with pytest.raises(ValueError, match="season"):
        retro_report(_advice(), other)


def _snapshot(roster_rows, consensus_rows, roster_slots):
    players = attach_weekly_points(roster_rows, consensus_rows)
    report = compare_lineup(players, roster_slots)
    return build_lineup_snapshot(
        season=2024,
        week=1,
        generated_at="2026-09-13T13:00:00Z",
        team_key="1.l.sit.t.1",
        team_name="Brian's Team",
        roster_slots=roster_slots,
        players=players,
        report=report,
    )


def _bundle(players):
    user_points = sum(row["points"] for row in players if row["team_key"] == "1.l.sit.t.1")
    return {
        "schema_version": 1,
        "source": "fixture",
        "synced_at": "2026-09-16T16:00:00Z",
        "league": {
            "league_id": "sit-1",
            "league_key": "1.l.sit",
            "name": "Sit Start Mock",
            "season": 2024,
            "week": 1,
            "num_teams": 2,
        },
        "matchups": [
            {
                "matchup_id": "1",
                "week": 1,
                "teams": [
                    {"team_key": "1.l.sit.t.1", "points": user_points},
                    {"team_key": "1.l.sit.t.2", "points": 10.0},
                ],
            }
        ],
        "players": players
        + [
            {
                "yahoo_player_id": "99901",
                "yahoo_player_key": "1.p.99901",
                "name": "Rival Receiver",
                "team_key": "1.l.sit.t.2",
                "selected_position": "WR",
                "points": 10.0,
            }
        ],
    }


def _actual_row(yahoo_player_id, name, selected_position, points):
    return {
        "yahoo_player_id": yahoo_player_id,
        "yahoo_player_key": f"1.p.{yahoo_player_id}",
        "name": name,
        "team_key": "1.l.sit.t.1",
        "selected_position": selected_position,
        "points": points,
    }


def test_retro_hindsight_surfaces_flex_that_advice_agreed_with():
    snapshot = _snapshot(
        [
            _player(
                yahoo_player_id="40904",
                yahoo_player_key="1.p.40904",
                full_name="Rico Dowdle",
                primary_position="RB",
                selected_position="W/R/T",
                player_key="dowdle",
                matched=True,
            ),
            _player(
                yahoo_player_id="40393",
                yahoo_player_key="1.p.40393",
                full_name="Malik Washington",
                primary_position="WR",
                eligible_positions=["WR", "W/T", "W/R/T"],
                selected_position="BN",
                player_key="washington",
                matched=True,
            ),
        ],
        [_consensus("dowdle", 12.0), _consensus("washington", 8.0)],
        {"W/R/T": 1, "BN": 2},
    )
    retro = retro_report(
        snapshot,
        _bundle(
            [
                _actual_row("40904", "Rico Dowdle", "W/R/T", 3.1),
                _actual_row("40393", "Malik Washington", "BN", 16.8),
            ]
        ),
    )
    assert retro["start_misses"] == []
    assert retro["sit_misses"] == []
    assert retro["delta"] == 0.0
    assert [row["name"] for row in retro["hindsight_start"]] == ["Malik Washington"]
    assert [row["name"] for row in retro["hindsight_sit"]] == ["Rico Dowdle"]
    assert retro["hindsight_delta"] > retro["delta"]
    assert retro["hindsight_total"] == 16.8
    assert retro["started_total"] == 3.1
    assert retro["hindsight_delta"] == 13.7


def test_retro_hindsight_keeps_advice_def_miss_without_changing_recommended_total():
    snapshot = _snapshot(
        [
            _player(
                yahoo_player_id="100001",
                yahoo_player_key="1.p.100001",
                full_name="Bad Def",
                primary_position="DEF",
                eligible_positions=["DEF"],
                selected_position="DEF",
                player_key="def:bad",
                matched=True,
            ),
            _player(
                yahoo_player_id="100002",
                yahoo_player_key="1.p.100002",
                full_name="Good Def",
                primary_position="DEF",
                eligible_positions=["DEF"],
                selected_position="BN",
                player_key="def:good",
                matched=True,
            ),
        ],
        [_consensus("def:bad", 8.0), _consensus("def:good", 12.0)],
        {"DEF": 1, "BN": 1},
    )
    retro = retro_report(
        snapshot,
        _bundle(
            [
                _actual_row("100001", "Bad Def", "DEF", 4.0),
                _actual_row("100002", "Good Def", "BN", 10.0),
            ]
        ),
    )
    assert retro["recommended_total"] == 10.0
    assert [row["name"] for row in retro["start_misses"]] == ["Good Def"]
    assert [row["name"] for row in retro["sit_misses"]] == ["Bad Def"]
    assert [row["name"] for row in retro["hindsight_start"]] == ["Good Def"]
    assert [row["name"] for row in retro["hindsight_sit"]] == ["Bad Def"]
    assert retro["hindsight_total"] == 10.0
    assert retro["delta"] == 6.0


def test_retro_hindsight_excludes_ir_and_il_actuals():
    snapshot = _snapshot(
        [
            _player(
                yahoo_player_id="55501",
                yahoo_player_key="1.p.55501",
                full_name="Slow Back",
                primary_position="RB",
                selected_position="RB",
                player_key="rb-low",
                matched=True,
            ),
            _player(
                yahoo_player_id="29279",
                yahoo_player_key="1.p.29279",
                full_name="Derrick Henry",
                selected_position="BN",
                player_key="12626",
                matched=True,
            ),
            _player(
                yahoo_player_id="il-1",
                yahoo_player_key="1.p.il-1",
                full_name="IL Back",
                primary_position="RB",
                selected_position="IL",
                player_key="il-back",
                matched=True,
            ),
        ],
        [
            _consensus("rb-low", 8.0),
            _consensus("12626", 6.0),
            _consensus("il-back", 4.0),
        ],
        {"RB": 1, "BN": 2, "IL": 1},
    )
    retro = retro_report(
        snapshot,
        _bundle(
            [
                _actual_row("55501", "Slow Back", "RB", 5.0),
                _actual_row("29279", "Derrick Henry", "IR", 30.0),
                _actual_row("il-1", "IL Back", "IL", 40.0),
            ]
        ),
    )
    names = {row["name"] for row in retro["hindsight_start"] + retro["hindsight_sit"]}
    assert "Derrick Henry" not in names
    assert "IL Back" not in names
    assert retro["hindsight_start"] == []
    assert retro["started_total"] == 5.0
    assert retro["hindsight_total"] == 5.0


def test_retro_hindsight_skips_swap_when_actuals_are_missing():
    snapshot = _snapshot(
        [
            _player(
                yahoo_player_id="55501",
                yahoo_player_key="1.p.55501",
                full_name="Slow Back",
                primary_position="RB",
                selected_position="RB",
                player_key="rb-low",
                matched=True,
            ),
            _player(
                yahoo_player_id="29279",
                yahoo_player_key="1.p.29279",
                full_name="Derrick Henry",
                selected_position="BN",
                player_key="12626",
                matched=True,
            ),
        ],
        [_consensus("rb-low", 12.0), _consensus("12626", 10.0)],
        {"RB": 1, "BN": 2},
    )
    actuals = _bundle([_actual_row("55501", "Slow Back", "RB", 2.0)])
    retro = retro_report(snapshot, actuals)
    assert retro["start_misses"] == []
    assert retro["hindsight_start"] == []
    assert retro["hindsight_sit"] == []
    assert [row["name"] for row in retro["missing_actuals"]] == ["Derrick Henry"]
    assert retro["hindsight_total"] == 2.0
    assert retro["hindsight_delta"] == 0.0


def test_retro_hindsight_tied_actuals_prefer_the_started_player():
    snapshot = _snapshot(
        [
            _player(
                yahoo_player_id="starter",
                yahoo_player_key="1.p.starter",
                full_name="Starter Back",
                primary_position="RB",
                selected_position="RB",
                player_key="starter",
                matched=True,
            ),
            _player(
                yahoo_player_id="bench",
                yahoo_player_key="1.p.bench",
                full_name="Bench Back",
                primary_position="RB",
                selected_position="BN",
                player_key="bench",
                matched=True,
            ),
        ],
        [_consensus("starter", 11.0), _consensus("bench", 9.0)],
        {"RB": 1, "BN": 1},
    )
    retro = retro_report(
        snapshot,
        _bundle(
            [
                _actual_row("starter", "Starter Back", "RB", 10.0),
                _actual_row("bench", "Bench Back", "BN", 10.0),
            ]
        ),
    )
    assert retro["hindsight_start"] == []
    assert retro["hindsight_sit"] == []
    assert retro["hindsight_total"] == 10.0
    assert retro["started_total"] == 10.0


def test_retro_hindsight_rb_cannot_steal_wt_from_te_or_wr():
    snapshot = _snapshot(
        [
            _player(
                yahoo_player_id="te-1",
                yahoo_player_key="1.p.te-1",
                full_name="Started TE",
                nfl_team="DET",
                primary_position="TE",
                eligible_positions=["TE", "W/T", "W/R/T"],
                selected_position="TE",
                player_key="te-1",
                matched=True,
            ),
            _player(
                yahoo_player_id="wr-1",
                yahoo_player_key="1.p.wr-1",
                full_name="Started WR",
                primary_position="WR",
                eligible_positions=["WR", "W/T", "W/R/T"],
                selected_position="W/T",
                player_key="wr-1",
                matched=True,
            ),
            _player(
                yahoo_player_id="rb-1",
                yahoo_player_key="1.p.rb-1",
                full_name="Bench RB",
                primary_position="RB",
                selected_position="BN",
                player_key="rb-1",
                matched=True,
            ),
        ],
        [_consensus("te-1", 8.0), _consensus("wr-1", 9.0), _consensus("rb-1", 4.0)],
        {"TE": 1, "W/T": 1, "BN": 1},
    )
    retro = retro_report(
        snapshot,
        _bundle(
            [
                _actual_row("te-1", "Started TE", "TE", 5.0),
                _actual_row("wr-1", "Started WR", "W/T", 6.0),
                _actual_row("rb-1", "Bench RB", "BN", 20.0),
            ]
        ),
    )
    assert retro["start_misses"] == []
    assert [row["name"] for row in retro["hindsight_start"]] == []
    assert [row["name"] for row in retro["hindsight_sit"]] == []
    assert retro["hindsight_total"] == 11.0
    assert retro["started_total"] == 11.0
