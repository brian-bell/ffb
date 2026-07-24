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

    class FakeFrame:
        columns = list(raw[0])

        def select(self, columns):
            return self

        def to_dicts(self):
            return raw

    monkeypatch.setattr(nflreadpy, "load_ff_playerids", lambda: FakeFrame())

    with caplog.at_level(logging.INFO, logger="ffb.sources.crosswalk"):
        rows = crosswalk.fetch_playerids()

    assert rows == raw
    assert "api request provider=nflverse operation=load_ff_playerids" in caplog.text
    assert (
        f"api response provider=nflverse operation=load_ff_playerids items={len(raw)}"
        in caplog.text
    )
