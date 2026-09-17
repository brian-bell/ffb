"""Pure Sleeper league mappers: slots, scoring, user team, DEF, starter zip."""

import json
from pathlib import Path

import pytest

from ffb import config
from ffb.league_context import roster_slot_counts
from ffb.sources import sleeper_league as sl

FIXTURES = Path(__file__).parent / "fixtures" / "sleeper"
USER_ID = "1395866680286003200"
SYNCED_AT = "2026-09-17T12:00:00Z"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def _state(**overrides):
    kwargs = {
        "league": _load("league.json"),
        "rosters": _load("rosters.json"),
        "users": _load("users.json"),
        "state": _load("state_nfl.json"),
        "user_id": USER_ID,
        "season": 2026,
        "synced_at": SYNCED_AT,
        "players_by_id": _load("players.json"),
    }
    return sl.map_state(**(kwargs | overrides))


def test_flex_slots_collapse_to_two_wrt():
    slots = sl.collapse_roster_positions(_load("league.json")["roster_positions"])
    by_pos = {slot["position"]: slot for slot in slots}
    assert "FLEX" not in by_pos
    assert by_pos["W/R/T"] == {"position": "W/R/T", "count": 2, "is_starting": True}
    assert by_pos["RB"]["count"] == 2
    assert by_pos["BN"] == {"position": "BN", "count": 5, "is_starting": False}
    assert roster_slot_counts(slots)["W/R/T"] == 2


def test_super_flex_and_idp_slots_fail_loud():
    with pytest.raises(ValueError, match="unsupported Sleeper roster slot"):
        sl.collapse_roster_positions(["QB", "SUPER_FLEX"])
    with pytest.raises(ValueError, match="unsupported Sleeper roster slot"):
        sl.map_slot("LB")


def test_full_ppr_scoring_map_never_falls_back_to_yahoo():
    scoring = sl.parse_scoring_settings(_load("league.json")["scoring_settings"])
    weights = scoring["scoring"].weights
    assert weights["rec"] == 1.0
    assert weights["pass_yd"] == 0.04
    assert weights["pass_int"] == -1.0
    assert weights["fum_lost"] == -2.0
    assert weights["fgm_50p"] == 5.0
    assert weights["fgm_60p"] == 6.0
    assert weights["def_fum_td"] == 6.0
    assert weights["pass_int_td"] == 6.0
    assert weights["def_ret_td"] == 6.0
    assert weights["pts_allow_35p"] == -4.0
    assert weights is not config.LEAGUE_SCORING.weights
    assert scoring["scoring"] is not config.LEAGUE_SCORING
    assert weights["rec"] != config.LEAGUE_SCORING.weights["rec"]
    assert "pass_int" not in config.LEAGUE_SCORING.weights


def test_zero_value_sleeper_settings_are_ignored():
    scoring = sl.parse_scoring_settings({"rec": 1.0, "fum": 0.0, "pts_allow_21_27": 0.0})
    assert "fum" not in scoring["weights"]
    assert "pts_allow_21_27" not in scoring["weights"]


def test_unsupported_bonus_fails_loud():
    with pytest.raises(ValueError, match="unsupported Sleeper scoring setting 'bonus_rec_te'"):
        sl.parse_scoring_settings({"rec": 1.0, "bonus_rec_te": 0.5})


def test_empty_or_unmapped_scoring_is_rejected():
    with pytest.raises(ValueError, match="nonempty"):
        sl.parse_scoring_settings({})
    with pytest.raises(ValueError, match="no nonzero"):
        sl.parse_scoring_settings({"fum": 0.0})


def test_is_user_team_is_unique_for_verified_user_id():
    state = _state()
    user_teams = [team for team in state.teams if team["is_user_team"]]
    assert len(user_teams) == 1
    assert user_teams[0]["team_id"] == "6"
    assert user_teams[0]["name"] == "Steelers Nation"
    assert user_teams[0]["team_key"] == f"{config.SLEEPER_LEAGUE_KEY}.t.6"
    assert sum(team["is_user_team"] for team in state.teams) == 1


def test_wrong_user_id_fails_uniqueness():
    with pytest.raises(ValueError, match="exactly one roster"):
        sl.map_state(
            league=_load("league.json"),
            rosters=_load("rosters.json"),
            users=_load("users.json"),
            state=_load("state_nfl.json"),
            user_id="000",
            season=2026,
            synced_at=SYNCED_AT,
            players_by_id=_load("players.json"),
        )


def test_two_rosters_for_the_same_user_fail_uniqueness():
    rosters = _load("rosters.json")
    rosters[1]["owner_id"] = USER_ID
    with pytest.raises(ValueError, match="exactly one roster"):
        sl.map_state(
            league=_load("league.json"),
            rosters=rosters,
            users=_load("users.json"),
            state=_load("state_nfl.json"),
            user_id=USER_ID,
            season=2026,
            synced_at=SYNCED_AT,
            players_by_id=_load("players.json"),
        )


def test_sf_defense_id_canonicalizes_to_def_sfo():
    roster = sl.parse_roster(
        _load("rosters.json")[0],
        roster_positions=_load("league.json")["roster_positions"],
        week=2,
        league_id=config.SLEEPER_LEAGUE_ID,
        players_by_id=_load("players.json"),
    )
    defense = next(player for player in roster["players"] if player["yahoo_player_id"] == "SF")
    assert defense["primary_position"] == "DEF"
    assert defense["nfl_team"] == "SFO"
    assert defense["selected_position"] == "DEF"
    # Team-code id without a players-map row still canonicalizes.
    bare = sl._parse_player("SF", None, selected_position="DEF")
    assert bare["nfl_team"] == "SFO"
    assert bare["primary_position"] == "DEF"


def test_starter_zip_assigns_flex_and_bench():
    roster = sl.parse_roster(
        _load("rosters.json")[0],
        roster_positions=_load("league.json")["roster_positions"],
        week=2,
        league_id=config.SLEEPER_LEAGUE_ID,
        players_by_id=_load("players.json"),
    )
    by_id = {player["yahoo_player_id"]: player for player in roster["players"]}
    assert by_id["slow"]["selected_position"] == "RB"
    assert by_id["7564"]["selected_position"] == "WR"
    assert by_id["flex"]["selected_position"] == "W/R/T"
    assert by_id["flex2"]["selected_position"] == "W/R/T"
    assert by_id["3198"]["selected_position"] == "BN"
    assert by_id["3198"]["yahoo_player_key"] == "sleeper:3198"
    assert roster["week"] == 2
    assert roster["team_key"] == f"{config.SLEEPER_LEAGUE_KEY}.t.6"


def test_taxi_slots_nonzero_fails_loud():
    league = _load("league.json")
    league["settings"]["taxi_slots"] = 2
    with pytest.raises(ValueError, match="taxi_slots"):
        sl.map_state(
            league=league,
            rosters=_load("rosters.json"),
            users=_load("users.json"),
            state=_load("state_nfl.json"),
            user_id=USER_ID,
            season=2026,
            synced_at=SYNCED_AT,
            players_by_id=_load("players.json"),
        )


def test_taxi_players_fail_loud():
    raw = _load("rosters.json")[0]
    raw["taxi"] = ["3198"]
    with pytest.raises(ValueError, match="taxi players are unsupported"):
        sl.parse_roster(
            raw,
            roster_positions=_load("league.json")["roster_positions"],
            week=2,
            league_id=config.SLEEPER_LEAGUE_ID,
            players_by_id=_load("players.json"),
        )


def test_reserve_player_is_ir_not_bench():
    raw = _load("rosters.json")[0]
    raw["reserve"] = ["3198"]
    roster = sl.parse_roster(
        raw,
        roster_positions=_load("league.json")["roster_positions"],
        week=2,
        league_id=config.SLEEPER_LEAGUE_ID,
        players_by_id=_load("players.json"),
    )
    henry = next(player for player in roster["players"] if player["yahoo_player_id"] == "3198")
    assert henry["selected_position"] == "IR"


def test_starter_length_mismatch_fails():
    raw = _load("rosters.json")[0]
    raw["starters"] = raw["starters"][:-1]
    with pytest.raises(ValueError, match="starters length"):
        sl.parse_roster(
            raw,
            roster_positions=_load("league.json")["roster_positions"],
            week=2,
            league_id=config.SLEEPER_LEAGUE_ID,
            players_by_id=_load("players.json"),
        )


def test_mapped_state_is_a_league_bundle_keyed_by_sleeper_league_and_nfl_state_week():
    bundle = _state()
    assert bundle.data["source"] == "sleeper"
    assert bundle.data["schema_version"] == 1
    assert bundle.league["league_key"] == config.SLEEPER_LEAGUE_KEY
    assert bundle.league["league_id"] == config.SLEEPER_LEAGUE_ID
    assert bundle.league["name"] == "2026-ff-nyt"
    assert bundle.league["current_week"] == 2
    assert bundle.league["num_teams"] == 2
    provider = bundle.settings["provider_settings"]
    assert provider["playoff_week_start"] == 15
    assert provider["taxi_slots"] == 0
    assert provider["max_keepers"] == 1
    slots = roster_slot_counts(bundle.settings["roster_slots"])
    assert slots["W/R/T"] == 2
    assert slots["BN"] == 5
    # Fail-loud scoring means nothing is ever silently dropped into unmapped.
    assert bundle.settings["unmapped_scoring_rules"] == []


def test_nfl_state_from_another_season_is_rejected():
    """state/nfl is snapshotted globally, so a stale replay must not set the week."""
    state = _load("state_nfl.json") | {"season": 2025}
    with pytest.raises(ValueError, match="does not match league season"):
        _state(state=state)


def test_player_return_tds_are_not_scored_as_the_defense():
    """st_td is a player's own return TD; _normalize_stats keeps def_ret_td for DEF."""
    parsed = sl.parse_scoring_settings({"st_td": 6.0, "def_st_td": 6.0, "rec": 1.0})
    weights = parsed["weights"]
    assert weights["pr_td"] == 6.0
    assert weights["def_kr_td"] == 6.0
    assert weights["def_ret_td"] == 6.0


def test_player_and_defense_return_tds_can_score_differently():
    """They collapsed onto one key before, so unequal values failed to load at all."""
    weights = sl.parse_scoring_settings({"st_td": 6.0, "def_st_td": 4.0})["weights"]
    assert weights["pr_td"] == 6.0
    assert weights["def_ret_td"] == 4.0


def test_season_mismatch_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        sl.map_state(
            league=_load("league.json"),
            rosters=_load("rosters.json"),
            users=_load("users.json"),
            state=_load("state_nfl.json"),
            user_id=USER_ID,
            season=2024,
            synced_at=SYNCED_AT,
            players_by_id=_load("players.json"),
        )


def test_resolve_batch_uses_sleeper_ids_not_yahoo():
    class _Store:
        def resolve_batch(self, source, native_ids):
            assert source == "sleeper"
            assert "3198" in native_ids
            assert "29279" not in native_ids
            return {
                "3198": {
                    "player_key": "12626",
                    "full_name": "Derrick Henry",
                    "position": "RB",
                    "team": "BAL",
                }
            }

    roster = sl.parse_roster(
        _load("rosters.json")[0],
        roster_positions=_load("league.json")["roster_positions"],
        week=2,
        league_id=config.SLEEPER_LEAGUE_ID,
        players_by_id=_load("players.json"),
    )
    rows = sl.resolve_sleeper_roster_rows(_Store(), roster["players"])
    henry = next(row for row in rows if row["yahoo_player_id"] == "3198")
    defense = next(row for row in rows if row["yahoo_player_id"] == "SF")
    unknown = next(row for row in rows if row["yahoo_player_id"] == "slow")
    assert henry["player_key"] == "12626"
    assert henry["matched"] is True
    assert defense["player_key"] == "def:SFO"
    assert defense["matched"] is True
    assert unknown["player_key"] == "sleeper:slow"
    assert unknown["matched"] is False


def test_resolve_recomputes_eligible_positions_from_final_position():
    """Stale Sleeper WR eligibility must not survive a crosswalk RB identity."""
    from ffb.lineup import can_fill

    player = sl._parse_player(
        "misfiled",
        {
            "first_name": "Misfiled",
            "last_name": "Back",
            "position": "WR",
            "team": "BAL",
        },
        selected_position="WR",
    )
    assert player["eligible_positions"] == ["WR", "W/R/T"]

    class _Store:
        def resolve_batch(self, source, native_ids):
            assert source == "sleeper"
            return {
                "misfiled": {
                    "player_key": "rb-xw",
                    "full_name": "Misfiled Back",
                    "position": "RB",
                    "team": "BAL",
                }
            }

    [row] = sl.resolve_sleeper_roster_rows(_Store(), [player])
    assert row["position"] == "RB"
    assert row["primary_position"] == "RB"
    assert row["eligible_positions"] == ["RB", "W/R/T"]
    assert can_fill(row, "RB") is True
    assert can_fill(row, "WR") is False
    assert can_fill(row, "W/R/T") is True


def test_resolve_def_overwrites_stale_skill_eligibility():
    player = {
        "yahoo_player_id": "SF",
        "yahoo_player_key": "sleeper:SF",
        "name": "49ers",
        "nfl_team": "SFO",
        "primary_position": "DEF",
        "eligible_positions": ["WR", "W/R/T"],
        "selected_position": "DEF",
    }

    class _Store:
        def resolve_batch(self, source, native_ids):
            return {}

    [row] = sl.resolve_sleeper_roster_rows(_Store(), [player])
    assert row["player_key"] == "def:SFO"
    assert row["position"] == "DEF"
    assert row["eligible_positions"] == ["DEF"]
