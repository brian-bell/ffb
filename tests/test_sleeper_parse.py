"""Parsing raw Sleeper projection rows into normalized records."""

import json
from pathlib import Path

import pytest

from ffb.config import FANTASY_POSITIONS
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


def test_excludes_non_allowlist_positions(raw):
    # Sleeper's position[] fetch filter isn't honored strictly (FB/P/CB rows leak
    # through), so parse re-checks the allowlist. The LB and FB rows are dropped.
    rows = parse_projections(raw)
    assert all(r["native_id"] not in ("4960", "1379") for r in rows)
    assert all(r["position"] in FANTASY_POSITIONS for r in rows)


def test_idp_opt_in_retains_row(raw):
    # Widening the allowlist with a superset keeps the LB; FB stays excluded.
    rows = parse_projections(raw, allowed_positions=set(FANTASY_POSITIONS) | {"LB"})
    roquan = next(r for r in rows if r["native_id"] == "4960")
    assert roquan["position"] == "LB"
    assert all(r["native_id"] != "1379" for r in rows)


def test_row_count(raw):
    # 12 raw rows: 2 dropped (null player, missing position), 1 dropped (company),
    # 2 dropped (LB/FB outside the position allowlist).
    assert len(parse_projections(raw)) == 7


def test_positions_present(raw):
    positions = {r["position"] for r in parse_projections(raw)}
    assert positions == {"RB", "QB", "WR", "K", "DEF"}
