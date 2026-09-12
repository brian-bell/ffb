"""Weekly retro: sit/start advice vs locked actuals."""

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
