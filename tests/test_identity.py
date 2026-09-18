"""Canonical team-defense identity is strict and source-independent."""

import pytest

from ffb.identity import (
    canonical_defense_key,
    canonical_team,
    defense_identity,
    is_defense_position,
)


@pytest.mark.parametrize(
    ("source_code", "canonical"),
    [("SF", "SFO"), ("KC", "KCC"), ("GB", "GBP"), ("JAX", "JAC")],
)
def test_canonical_team_aliases(source_code, canonical):
    assert canonical_team(source_code) == canonical


def test_def_and_dst_share_the_same_key():
    assert canonical_defense_key("DEF", "SF") == ("def:SFO", "SFO")
    assert canonical_defense_key("DST", "SFO") == ("def:SFO", "SFO")
    assert canonical_defense_key("D/ST", "SF") == ("def:SFO", "SFO")


def test_unknown_team_cannot_claim_a_canonical_defense_key():
    assert canonical_defense_key("DEF", "FA") is None


def test_canonical_team_maps_nflverse_schedule_codes():
    # nflverse schedules label the Rams "LA" (spike-verified 2026-07-23); every
    # other schedule code is already canonical or covered by an existing alias.
    assert canonical_team("LA") == "LAR"
    # Retired relocation codes still appear in stale crosswalk rows.
    assert canonical_team("OAK") == "LVR"


@pytest.mark.parametrize(
    ("label", "canonical"),
    [
        ("Rams", "LAR"),
        ("Rams D/ST", "LAR"),
        ("Los Angeles Rams", "LAR"),
        ("Ravens", "BAL"),
        ("Baltimore Ravens", "BAL"),
        ("Broncos", "DEN"),
        ("Denver Broncos", "DEN"),
        ("Denver Broncos Defense", "DEN"),
    ],
)
def test_canonical_team_maps_opsbot_defense_names(label, canonical):
    """Yahoo/Sleeper DEF display names must resolve like SF → SFO."""
    assert canonical_team(label) == canonical
    assert canonical_defense_key("DEF", label) == (f"def:{canonical}", canonical)


def test_ambiguous_city_names_are_not_guessed():
    assert canonical_team("Los Angeles") is None
    assert canonical_team("New York") is None


def test_defense_identity_prefers_team_code_then_name():
    assert defense_identity("DEF", "LAR", "Rams") == ("def:LAR", "LAR")
    assert defense_identity("D/ST", None, "Ravens") == ("def:BAL", "BAL")
    assert defense_identity("DEF", "FA", "Denver Broncos") == ("def:DEN", "DEN")
    assert defense_identity("RB", "Rams") is None
    assert is_defense_position("D/ST") is True
    assert is_defense_position("WR") is False
