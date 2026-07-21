"""Parsing raw Sleeper projection rows into normalized records."""

import json
from pathlib import Path

import pytest

from ffb.sources.sleeper import parse_projections

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper_projections_sample.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text())


def test_parses_valid_rows(raw):
    rows = parse_projections(raw)
    henry = next(r for r in rows if r["native_id"] == "3198")
    assert henry["full_name"] == "Derrick Henry"
    assert henry["position"] == "RB"
    assert henry["team"] == "BAL"
    assert henry["season"] == 2024
    assert henry["source"] == "sleeper"
    assert henry["scope"] == "season"
    assert henry["src_pts_ppr"] == 288.0
    assert henry["stats"]["rush_yd"] == 1575.0


def test_skips_null_player_rows(raw):
    rows = parse_projections(raw)
    assert all(r["native_id"] != "9999" for r in rows)


def test_skips_missing_position_rows(raw):
    rows = parse_projections(raw)
    assert all(r["native_id"] != "8888" for r in rows)


def test_keeps_only_configured_company(raw):
    # Derrick Henry appears twice (rotowire + some_other_company); keep one.
    henry_rows = [r for r in parse_projections(raw) if r["native_id"] == "3198"]
    assert len(henry_rows) == 1
    assert henry_rows[0]["src_pts_ppr"] == 288.0  # the rotowire row


def test_row_count(raw):
    # 8 raw rows: 2 dropped (null player, missing position), 1 dropped (company).
    assert len(parse_projections(raw)) == 5


def test_positions_present(raw):
    positions = {r["position"] for r in parse_projections(raw)}
    assert positions == {"RB", "QB", "WR"}
