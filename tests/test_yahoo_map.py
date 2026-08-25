"""Pure raw-Yahoo -> LeagueBundle mapping against hand-authored fixtures.

The fixtures model documented Yahoo Fantasy JSON quirks: the fantasy_content
wrapper, integer-string-keyed collections with a count key, and positional
arrays mixing dicts and lists. They are modeled from documentation, not
observed traffic; ffb-1ct.2 corrects them against real responses.
"""

import json
from pathlib import Path

import pytest

from ffb.league import parse_bundle
from ffb.sources import yahoo

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"
SYNCED_AT = "2026-08-24T12:00:00Z"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def _raw():
    return {
        "meta": _load("league_meta.json"),
        "settings": _load("league_settings.json"),
        "teams": _load("league_teams.json"),
        "rosters": [_load("roster_team1.json"), _load("roster_team2.json")],
    }


def _bundle_payload():
    raw = _raw()
    return yahoo.map_bundle(
        meta=raw["meta"],
        settings=raw["settings"],
        teams=raw["teams"],
        rosters=raw["rosters"],
        synced_at=SYNCED_AT,
    )


def test_mapped_bundle_passes_the_closed_schema_contract():
    bundle = parse_bundle(_bundle_payload(), season=2026)
    assert bundle.data["source"] == "yahoo"
    assert bundle.data["synced_at"] == SYNCED_AT


def test_league_meta_coerces_string_integers():
    league = _bundle_payload()["league"]
    assert league == {
        "league_id": "12345",
        "league_key": "461.l.12345",
        "name": "Test League",
        "season": 2026,
        "current_week": 2,
        "num_teams": 2,
    }


def test_roster_slots_mark_bench_and_ir_as_non_starting():
    slots = {s["position"]: s for s in _bundle_payload()["settings"]["roster_slots"]}
    assert slots["W/R/T"] == {"position": "W/R/T", "count": 2, "is_starting": True}
    assert slots["BN"]["is_starting"] is False
    assert slots["IR"] == {"position": "IR", "count": 2, "is_starting": False}


def test_scoring_rules_use_static_stat_map_and_fan_out_two_point_conversions():
    settings = _bundle_payload()["settings"]
    by_key = {r["stat_key"]: r for r in settings["scoring_rules"]}
    assert by_key["pass_yd"]["points"] == 0.05
    assert by_key["pass_yd"]["provider_stat_id"] == "4"
    assert by_key["pass_yd"]["provider_name"] == "Passing Yards"
    assert by_key["rec"]["points"] == 0.5
    assert by_key["sack"]["points"] == 1.0
    # Yahoo's single 2-PT category covers pass/rush/rec; the map fans it out.
    for key in ("pass_2pt", "rush_2pt", "rec_2pt"):
        assert by_key[key]["points"] == 2.0
        assert by_key[key]["provider_stat_id"].startswith("16")
        assert by_key[key]["provider_name"] == "2-Point Conversions"


def test_unmappable_stats_are_surfaced_not_dropped():
    settings = _bundle_payload()["settings"]
    assert settings["unmapped_scoring_rules"] == [
        {"points": 6.0, "provider_stat_id": "15", "provider_name": "Return Touchdowns"}
    ]


def test_provider_settings_keep_only_scalars():
    provider = _bundle_payload()["settings"]["provider_settings"]
    assert provider["scoring_type"] == "head"
    assert all(isinstance(v, (str, int, float, bool)) for v in provider.values())


def test_teams_flatten_positional_fragments_and_detect_user_team():
    teams = _bundle_payload()["teams"]
    assert teams == [
        {
            "team_id": "1",
            "team_key": "461.l.12345.t.1",
            "name": "Brian's Best",
            "managers": ["Brian"],
            "is_user_team": True,
        },
        {
            "team_id": "2",
            "team_key": "461.l.12345.t.2",
            "name": "Rival Squad",
            "managers": ["Alex", "Sam"],
            "is_user_team": False,
        },
    ]


def test_rosters_normalize_teams_and_free_agents():
    rosters = {r["team_key"]: r for r in _bundle_payload()["rosters"]}
    henry, dst = rosters["461.l.12345.t.1"]["players"]
    assert henry == {
        "yahoo_player_id": "29279",
        "yahoo_player_key": "461.p.29279",
        "name": "Derrick Henry",
        "nfl_team": "BAL",
        "primary_position": "RB",
        "eligible_positions": ["RB", "W/R/T"],
        "selected_position": "RB",
    }
    assert dst["primary_position"] == "DEF"
    assert dst["nfl_team"] == "BAL"
    teamless = rosters["461.l.12345.t.2"]["players"][1]
    assert teamless["nfl_team"] is None
    assert teamless["selected_position"] == "BN"


def test_missing_fantasy_content_wrapper_is_rejected():
    with pytest.raises(ValueError, match="fantasy_content"):
        yahoo.parse_league_meta({"league": []})


def test_malformed_roster_player_rejects_the_whole_roster():
    """A partial roster must never atomically replace stored league state."""
    raw = _load("roster_team1.json")
    players = raw["fantasy_content"]["team"][1]["roster"]["0"]["players"]
    players["0"]["player"] = "garbage"
    with pytest.raises(ValueError, match="roster player"):
        yahoo.parse_roster(raw)


def test_malformed_stat_modifier_rejects_the_settings():
    """A dropped rule would silently misprice players, so parsing must fail."""
    raw = _load("league_settings.json")
    stats = raw["fantasy_content"]["league"][1]["settings"][0]["stat_modifiers"]["stats"]
    stats[0]["stat"]["value"] = "not-a-number"
    with pytest.raises(ValueError, match="stat modifier"):
        yahoo.parse_settings(raw)


def test_malformed_team_rejects_the_teams_parse():
    """A partial team list must never become a persisted snapshot."""
    raw = _load("league_teams.json")
    raw["fantasy_content"]["league"][1]["teams"]["1"]["team"] = "garbage"
    with pytest.raises(ValueError, match="team"):
        yahoo.parse_teams(raw)


def test_settings_without_stat_modifiers_are_rejected():
    """A truncated settings payload must not promote empty scoring rules."""
    raw = _load("league_settings.json")
    del raw["fantasy_content"]["league"][1]["settings"][0]["stat_modifiers"]
    with pytest.raises(ValueError, match="stat_modifiers"):
        yahoo.parse_settings(raw)


def test_malformed_roster_position_rejects_the_settings():
    raw = _load("league_settings.json")
    positions = raw["fantasy_content"]["league"][1]["settings"][0]["roster_positions"]
    positions[0]["roster_position"] = "garbage"
    with pytest.raises(ValueError, match="roster position"):
        yahoo.parse_settings(raw)


def test_roster_position_without_count_rejects_the_settings():
    raw = _load("league_settings.json")
    positions = raw["fantasy_content"]["league"][1]["settings"][0]["roster_positions"]
    del positions[0]["roster_position"]["count"]
    with pytest.raises(ValueError, match="roster position"):
        yahoo.parse_settings(raw)


@pytest.mark.parametrize(
    ("fixture", "path", "parser"),
    [
        ("league_meta.json", ("league", 0, "league_key"), "parse_league_meta"),
        ("league_teams.json", ("league", 1, "teams", "0", "team", 0, 0, "team_key"), "parse_teams"),
    ],
)
def test_null_identifiers_are_rejected_not_stringified(fixture, path, parser):
    """A null id must fail validation, not become the string 'None'."""
    raw = _load(fixture)
    node = raw["fantasy_content"]
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = None
    with pytest.raises(ValueError, match="nonempty string"):
        getattr(yahoo, parser)(raw)


def test_null_player_id_rejects_the_roster():
    raw = _load("roster_team1.json")
    fragments = raw["fantasy_content"]["team"][1]["roster"]["0"]["players"]["0"]["player"][0]
    fragments[1] = {"player_id": None}
    with pytest.raises(ValueError, match="roster player"):
        yahoo.parse_roster(raw)


def test_roster_without_players_collection_is_rejected():
    """An omitted players field must not parse as a valid empty roster."""
    raw = _load("roster_team1.json")
    del raw["fantasy_content"]["team"][1]["roster"]["0"]["players"]
    with pytest.raises(ValueError, match="players"):
        yahoo.parse_roster(raw)


def test_explicit_zero_count_roster_is_a_legitimate_empty_roster():
    raw = _load("roster_team1.json")
    raw["fantasy_content"]["team"][1]["roster"]["0"]["players"] = {"count": 0}
    assert yahoo.parse_roster(raw)["players"] == []


def test_truncated_count_keyed_collection_is_rejected():
    """count=2 with one entry means a truncated roster, not a complete one."""
    raw = _load("roster_team1.json")
    players = raw["fantasy_content"]["team"][1]["roster"]["0"]["players"]
    del players["1"]  # count stays 2
    with pytest.raises(ValueError, match="count"):
        yahoo.parse_roster(raw)


def test_gapped_count_keyed_collection_is_rejected():
    raw = _load("league_teams.json")
    teams = raw["fantasy_content"]["league"][1]["teams"]
    teams["2"] = teams.pop("1")  # keys 0 and 2: entry 1 went missing
    teams["count"] = 2
    with pytest.raises(ValueError, match="contiguous"):
        yahoo.parse_teams(raw)


def test_indexed_collections_tolerate_plain_lists():
    raw = _load("league_teams.json")
    teams_node = raw["fantasy_content"]["league"][1]["teams"]
    as_list = [teams_node["0"], teams_node["1"]]
    raw["fantasy_content"]["league"][1]["teams"] = as_list
    assert len(yahoo.parse_teams(raw)) == 2


def test_every_mapped_stat_key_is_a_known_scoring_key():
    """The static map must emit keys the scoring configs understand."""
    from ffb import config

    known = (
        set(config.LEAGUE_SCORING.weights)
        | set(config.DEFAULT_PPR.weights)
        | {
            "fgm_0_19",
            "fgm_20_29",
            "fgm_30_39",
            "fgm_40_49",
            "fgm_50p",
            "xpm",
            "pts_allow_28_34",
            "pts_allow_35p",
        }
    )
    for keys in config.YAHOO_STAT_MAP.values():
        for key in keys:
            assert key in known, key
