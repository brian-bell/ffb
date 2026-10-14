"""Crosswalk parse: nflverse ff_playerids records -> normalized spine rows."""

import json
import logging
from pathlib import Path

import nflreadpy

from ffb.sources import crosswalk
from ffb.sources.crosswalk import parse_crosswalk

FIXTURE = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"


def _rows():
    return parse_crosswalk(json.loads(FIXTURE.read_text()))


def test_parses_canonical_row_with_string_ids():
    henry = next(r for r in _rows() if r["full_name"] == "Derrick Henry")
    # mfl_id is the canonical player_key; numeric ids become strings for joins.
    assert henry["player_key"] == "12626"
    assert henry["sleeper_id"] == "3198"
    assert henry["espn_id"] == "3043078"
    assert henry["yahoo_id"] == "29279"
    assert henry["gsis_id"] == "00-0032764"
    assert henry["position"] == "RB"
    assert henry["team"] == "BAL"


def _sleeper_player(
    player_id: str,
    first: str,
    last: str,
    position: str,
    **ids: object,
) -> dict:
    return {
        "player_id": player_id,
        "first_name": first,
        "last_name": last,
        "position": position,
        **ids,
    }


def test_null_sleeper_id_fills_from_unique_rotowire_id():
    # nflverse has Trey Smack (mfl 17668, PK) with sleeper_id null. Sleeper's
    # player map has id 13545 and the same rotowire_id, and no espn/gsis id.
    rows = parse_crosswalk(
        [
            {
                "mfl_id": 17668,
                "name": "Trey Smack",
                "position": "PK",
                "team": "GBP",
                "sleeper_id": None,
                "espn_id": 4869461,
                "gsis_id": "00-0040899",
                "rotowire_id": 19511,
            }
        ],
        sleeper_players={
            "13545": _sleeper_player(
                "13545", "Trey", "Smack", "K", rotowire_id=19511, espn_id=None, gsis_id=None
            )
        },
    )
    smack = rows[0]
    assert smack["player_key"] == "17668"
    assert smack["position"] == "K"
    assert smack["sleeper_id"] == "13545"
    assert smack["espn_id"] == "4869461"
    assert "rotowire_id" not in smack


def test_sleeper_id_link_uses_espn_or_sportradar_when_that_id_is_unique():
    rows = parse_crosswalk(
        [
            {
                "mfl_id": 10,
                "name": "Espn Only",
                "position": "WR",
                "team": "GB",
                "espn_id": 111,
            },
            {
                "mfl_id": 11,
                "name": "Radar Only",
                "position": "RB",
                "team": "DAL",
                "sportradar_id": "sr-1",
            },
        ],
        sleeper_players={
            "42": _sleeper_player("42", "Espn", "Only", "WR", espn_id=111),
            "43": _sleeper_player("43", "Radar", "Only", "RB", sportradar_id="sr-1"),
        },
    )
    by_key = {row["player_key"]: row["sleeper_id"] for row in rows}
    assert by_key == {"10": "42", "11": "43"}


def test_existing_sleeper_id_is_not_replaced():
    rows = parse_crosswalk(
        [
            {
                "mfl_id": 12626,
                "name": "Derrick Henry",
                "position": "RB",
                "team": "BAL",
                "sleeper_id": 3198,
                "rotowire_id": 999,
            }
        ],
        sleeper_players={
            "13545": _sleeper_player("13545", "Derrick", "Henry", "RB", rotowire_id=999)
        },
    )
    assert rows[0]["sleeper_id"] == "3198"


def test_collided_rotowire_id_blocks_a_unique_espn_id():
    # Two Sleeper records share rotowire 19511. ESPN 4869461 belongs to only
    # one of them. The shared id rejects the link; it is not treated as unpublished.
    rows = parse_crosswalk(
        [
            {
                "mfl_id": 17668,
                "name": "Trey Smack",
                "position": "PK",
                "team": "GBP",
                "espn_id": 4869461,
                "rotowire_id": 19511,
            }
        ],
        sleeper_players={
            "13545": _sleeper_player(
                "13545", "Trey", "Smack", "K", rotowire_id=19511, espn_id=4869461
            ),
            "99999": _sleeper_player("99999", "Other", "Player", "K", rotowire_id=19511),
        },
    )
    assert rows[0]["sleeper_id"] is None


def test_sleeper_id_link_skips_disagreement_claims_and_name_or_position_conflicts():
    rows = parse_crosswalk(
        [
            {
                "mfl_id": 1,
                "name": "Derrick Henry",
                "position": "RB",
                "team": "BAL",
                "sleeper_id": 3198,
            },
            {
                "mfl_id": 2,
                "name": "Trey Smack",
                "position": "PK",
                "team": "GBP",
                "rotowire_id": 19511,
                "espn_id": 4869461,
            },
            {
                "mfl_id": 3,
                "name": "Claimed Kicker",
                "position": "PK",
                "team": "DAL",
                "rotowire_id": 50,
            },
            {
                "mfl_id": 4,
                "name": "Matthew Hibner",
                "position": "TE",
                "team": "FA",
                "rotowire_id": 60,
            },
            {
                "mfl_id": 5,
                "name": "Max Bredeson",
                "position": "TE",
                "team": "MIN",
                "rotowire_id": 70,
            },
            {
                "mfl_id": 6,
                "name": "Twin Player",
                "position": "WR",
                "team": "FA",
                "rotowire_id": 80,
            },
            {
                "mfl_id": 7,
                "name": "Twin Player",
                "position": "WR",
                "team": "FA",
                "rotowire_id": 80,
            },
            {
                "mfl_id": 8,
                "name": "Ambiguous Id",
                "position": "RB",
                "team": "FA",
                "rotowire_id": 90,
            },
        ],
        sleeper_players={
            "13545": _sleeper_player("13545", "Trey", "Smack", "K", rotowire_id=19511),
            "9999": _sleeper_player("9999", "Trey", "Smack", "K", espn_id=4869461),
            "3198": _sleeper_player("3198", "Claimed", "Kicker", "K", rotowire_id=50),
            "13324": _sleeper_player("13324", "Matt", "Hibner", "TE", rotowire_id=60),
            "13516": _sleeper_player("13516", "Max", "Bredeson", "RB", rotowire_id=70),
            "80": _sleeper_player("80", "Twin", "Player", "WR", rotowire_id=80),
            "91": _sleeper_player("91", "Ambiguous", "Id", "RB", rotowire_id=90),
            "92": _sleeper_player("92", "Ambiguous", "Id", "RB", rotowire_id=90),
        },
    )
    by_key = {row["player_key"]: row["sleeper_id"] for row in rows}
    assert by_key["1"] == "3198"
    assert by_key["2"] is None  # rotowire and espn disagree
    assert by_key["3"] is None  # 3198 is already Henry's sleeper id
    assert by_key["4"] is None  # Matthew vs Matt
    assert by_key["5"] is None  # TE vs RB
    assert by_key["6"] is None  # two canonicals, one sleeper id
    assert by_key["7"] is None
    assert by_key["8"] is None  # two Sleeper records share rotowire 90


def test_place_kicker_position_normalized_to_k():
    # nflverse labels kickers "PK"; we normalize to "K" so matched kickers align
    # with the sources' and league's vocabulary and `--pos K` finds them.
    rows = parse_crosswalk([{"mfl_id": 1, "name": "Some Kicker", "position": "PK", "team": "BAL"}])
    assert rows[0]["position"] == "K"


def test_missing_ids_become_none():
    rookie = next(r for r in _rows() if r["full_name"] == "Rookie Wideout")
    assert rookie["sleeper_id"] is None
    assert rookie["yahoo_id"] is None
    assert rookie["gsis_id"] is None
    assert rookie["espn_id"] == "4500000"  # present ids still resolve


def test_empty_yahoo_id_fills_from_unique_stats_id():
    # nflverse leaves Kyle Monangai's yahoo_id null; stats_id 42025 is his Yahoo
    # identity and maps to exactly one canonical (mfl 17066).
    rows = parse_crosswalk(
        [
            {
                "mfl_id": 17066,
                "name": "Kyle Monangai",
                "position": "RB",
                "team": "CHI",
                "yahoo_id": None,
                "stats_id": 42025,
                "sleeper_id": 12534,
                "espn_id": 4608686,
            },
            {
                "mfl_id": 12626,
                "name": "Derrick Henry",
                "position": "RB",
                "team": "BAL",
                "yahoo_id": "29279",
                "stats_id": 29279,
            },
        ]
    )
    monangai = next(r for r in rows if r["player_key"] == "17066")
    henry = next(r for r in rows if r["player_key"] == "12626")
    assert monangai["yahoo_id"] == "42025"
    assert henry["yahoo_id"] == "29279"
    assert "stats_id" not in monangai


def test_ambiguous_stats_id_does_not_fill_yahoo_id():
    # Same stats_id on two canonicals: do not guess either yahoo_id.
    rows = parse_crosswalk(
        [
            {"mfl_id": 17544, "name": "Eli Stowers", "yahoo_id": None, "stats_id": 42676},
            {"mfl_id": 17584, "name": "Gabe Jacas", "yahoo_id": None, "stats_id": 42676},
            {
                "mfl_id": 17066,
                "name": "Kyle Monangai",
                "yahoo_id": None,
                "stats_id": 42025,
            },
        ]
    )
    by_key = {r["player_key"]: r for r in rows}
    assert by_key["17544"]["yahoo_id"] is None
    assert by_key["17584"]["yahoo_id"] is None
    assert by_key["17066"]["yahoo_id"] == "42025"


def test_existing_yahoo_id_is_not_replaced_by_stats_id():
    rows = parse_crosswalk(
        [
            {
                "mfl_id": 15759,
                "name": "John Metchie",
                "yahoo_id": "33999",
                "stats_id": 34063,
            }
        ]
    )
    assert rows[0]["yahoo_id"] == "33999"


def test_claimed_yahoo_id_blocks_unique_stats_id_fill():
    # A already owns yahoo_id 42025; B's stats_id is unique but collides with
    # that claim. Filling B would make resolve("yahoo", "42025") ambiguous.
    rows = parse_crosswalk(
        [
            {
                "mfl_id": 1,
                "name": "Already Matched",
                "yahoo_id": "42025",
                "stats_id": None,
            },
            {
                "mfl_id": 17066,
                "name": "Kyle Monangai",
                "yahoo_id": None,
                "stats_id": 42025,
            },
            {
                "mfl_id": 2,
                "name": "Unclaimed Rookie",
                "yahoo_id": None,
                "stats_id": 99999,
            },
        ]
    )
    by_key = {r["player_key"]: r for r in rows}
    assert by_key["1"]["yahoo_id"] == "42025"
    assert by_key["17066"]["yahoo_id"] is None
    assert by_key["2"]["yahoo_id"] == "99999"


def test_row_without_mfl_id_is_skipped():
    # No canonical key => cannot join; drop rather than invent a key.
    assert all(r["full_name"] != "No Canonical Key" for r in _rows())


def test_extra_columns_are_dropped():
    henry = next(r for r in _rows() if r["full_name"] == "Derrick Henry")
    assert set(henry) == {
        "player_key",
        "full_name",
        "position",
        "team",
        "sleeper_id",
        "espn_id",
        "yahoo_id",
        "gsis_id",
    }


def test_fetch_logs_nflreadpy_operation_and_returned_row_count(monkeypatch, caplog):
    raw = json.loads(FIXTURE.read_text())
    selected: list[str] = []

    class FakeFrame:
        columns = [*list(raw[0]), "stats_id"]

        def select(self, columns):
            selected.extend(columns)
            return self

        def to_dicts(self):
            return raw

    monkeypatch.setattr(nflreadpy, "load_ff_playerids", lambda: FakeFrame())

    with caplog.at_level(logging.INFO, logger="ffb.sources.crosswalk"):
        rows = crosswalk.fetch_playerids()

    assert rows == raw
    assert "stats_id" in selected
    assert "api request provider=nflverse operation=load_ff_playerids" in caplog.text
    assert (
        f"api response provider=nflverse operation=load_ff_playerids items={len(raw)}"
        in caplog.text
    )
