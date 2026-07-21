"""FFC ADP source: fetch key + pure parse to normalized rows."""

import json
from pathlib import Path

from ffb.sources.ffc import parse_adp, snapshot_key

FIXTURE = Path(__file__).parent / "fixtures" / "ffc_adp_sample.json"


def _raw():
    return json.loads(FIXTURE.read_text())


def _by_name(rows, name):
    return next(r for r in rows if r["full_name"] == name)


def test_snapshot_key_encodes_format_teams_season():
    assert snapshot_key(2024, teams=12, fmt="ppr") == "ffc/adp_ppr_12_2024"


def test_parse_returns_a_row_per_player():
    rows = parse_adp(_raw(), 2024)
    assert len(rows) == 11
    mccaffrey = _by_name(rows, "Christian McCaffrey")
    assert mccaffrey["native_id"] == "2749"  # stringified
    assert mccaffrey["position"] == "RB"
    assert mccaffrey["adp"] == 1.4
    assert mccaffrey["adp_high"] == 1
    assert mccaffrey["adp_low"] == 5
    assert mccaffrey["adp_stdev"] == 0.7
    assert mccaffrey["times_drafted"] == 1010
    assert mccaffrey["bye"] == 9
    assert mccaffrey["season"] == 2024
    assert mccaffrey["source"] == "ffc"


def test_parse_remaps_pk_to_k_and_keeps_def():
    rows = parse_adp(_raw(), 2024)
    assert _by_name(rows, "Justin Tucker")["position"] == "K"  # PK -> K
    assert _by_name(rows, "San Francisco Defense")["position"] == "DEF"


def test_parse_aliases_team_codes_to_nflverse_style():
    rows = parse_adp(_raw(), 2024)
    assert _by_name(rows, "Christian McCaffrey")["team"] == "SFO"  # SF -> SFO
    assert _by_name(rows, "Patrick Mahomes")["team"] == "KCC"  # KC -> KCC
    # A code already in nflverse style is untouched.
    assert _by_name(rows, "Ja'Marr Chase")["team"] == "CIN"


def test_parse_non_success_status_returns_empty():
    assert parse_adp({"status": "Error", "players": [{"player_id": 1}]}, 2024) == []
    assert parse_adp({}, 2024) == []


def test_parse_skips_malformed_row_without_raising():
    raw = {
        "status": "Success",
        "players": [
            {
                "player_id": 1,
                "name": "Good Player",
                "position": "RB",
                "team": "DAL",
                "adp": 5.0,
                "high": 3,
                "low": 8,
                "stdev": 1.0,
                "times_drafted": 100,
                "bye": 7,
            },
            {"player_id": 2, "name": "No Position", "team": "DAL"},
            {"name": "No Id", "position": "WR"},
        ],
    }
    rows = parse_adp(raw, 2024)
    assert [r["full_name"] for r in rows] == ["Good Player"]


def test_parse_skips_non_object_entries_without_raising():
    # A successful pull can still carry a junk entry (null / string). The parser
    # promises to skip, never raise — and its is_valid refresh gate depends on it.
    raw = {
        "status": "Success",
        "players": [
            None,
            "not-a-dict",
            {
                "player_id": 1,
                "name": "Good Player",
                "position": "RB",
                "team": "DAL",
                "adp": 5.0,
                "high": 3,
                "low": 8,
                "stdev": 1.0,
                "times_drafted": 100,
                "bye": 7,
            },
        ],
    }
    rows = parse_adp(raw, 2024)
    assert [r["full_name"] for r in rows] == ["Good Player"]


def test_parse_non_dict_response_returns_empty_without_raising():
    # An HTTP-200 payload that isn't the expected object (list/string) must follow
    # the invalid-pull path (return []), not raise inside the is_valid callback.
    assert parse_adp(["upstream error"], 2024) == []
    assert parse_adp("nope", 2024) == []
    assert parse_adp(None, 2024) == []


def test_parse_skips_rows_with_non_string_name_or_position():
    # A non-string name/position would crash name-resolution downstream (and, with
    # no per-row guard there, take the whole ADP ingest down). Reject at the parser.
    raw = {
        "status": "Success",
        "players": [
            {"player_id": 1, "name": ["bad"], "position": "RB", "team": "DAL"},
            {"player_id": 2, "name": "Bad Pos", "position": ["WR"], "team": "DAL"},
            {
                "player_id": 3,
                "name": "Good Player",
                "position": "RB",
                "team": "DAL",
                "adp": 5.0,
                "high": 3,
                "low": 8,
                "stdev": 1.0,
                "times_drafted": 100,
                "bye": 7,
            },
        ],
    }
    rows = parse_adp(raw, 2024)
    assert [r["full_name"] for r in rows] == ["Good Player"]
