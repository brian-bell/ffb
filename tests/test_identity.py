"""Canonical team-defense identity is strict and source-independent."""

import pytest

from ffb.identity import canonical_defense_key, canonical_team


@pytest.mark.parametrize(
    ("source_code", "canonical"),
    [("SF", "SFO"), ("KC", "KCC"), ("GB", "GBP"), ("JAX", "JAC")],
)
def test_canonical_team_aliases(source_code, canonical):
    assert canonical_team(source_code) == canonical


def test_def_and_dst_share_the_same_key():
    assert canonical_defense_key("DEF", "SF") == ("def:SFO", "SFO")
    assert canonical_defense_key("DST", "SFO") == ("def:SFO", "SFO")


def test_unknown_team_cannot_claim_a_canonical_defense_key():
    assert canonical_defense_key("DEF", "FA") is None


def test_canonical_team_maps_nflverse_schedule_codes():
    # nflverse schedules label the Rams "LA" (spike-verified 2026-07-23); every
    # other schedule code is already canonical or covered by an existing alias.
    assert canonical_team("LA") == "LAR"
