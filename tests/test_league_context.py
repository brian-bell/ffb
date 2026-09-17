"""Stored league settings override complete components, and never cross leagues."""

import pytest

from ffb import config
from ffb.league_context import LeagueSettingsUnavailable, load_league_context


class _Store:
    def __init__(self, value):
        self.value = value

    def league_context(self, season, league_key=None):
        return self.value


def _state(**changes):
    state = {
        "league_key": "470.l.928421",
        "scoring_rules": [{"stat_key": "pass_yd", "points": 1.0}],
        "unmapped_scoring_rules": [],
        "roster_slots": [
            {"position": "QB", "count": 1, "is_starting": True},
            {"position": "W/T", "count": 1, "is_starting": True},
            {"position": "IR", "count": 2, "is_starting": False},
            {"position": "BN", "count": 5, "is_starting": False},
        ],
        "num_teams": 10,
        "source": "fixture",
    }
    state.update(changes)
    return state


def test_complete_fixture_context_supplies_scoring_roster_and_team_count():
    context = load_league_context(_Store(_state()), 2024)
    assert context.scoring.weights == {"pass_yd": 1.0}
    assert context.roster_slots == {"QB": 1, "W/T": 1, "BN": 5}
    assert context.num_teams == 10
    assert context.scoring_provenance == "synced-yahoo:470.l.928421"
    assert context.league_key == "yahoo:470.l.928421"
    assert context.unmodeled_scoring == ()


def test_incomplete_components_fall_back_independently_within_the_same_league():
    """A Yahoo-shaped league may borrow the configured values: they are its own."""
    state = _state(
        scoring_rules=[],
        unmapped_scoring_rules=[{"points": 1.0, "provider_name": "bonus_rec_te"}],
        roster_slots=[{"position": "IDP", "count": 1, "is_starting": True}],
        num_teams=8,
    )
    context = load_league_context(_Store(state), 2024)
    assert context.scoring is config.LEAGUE_SCORING
    assert context.roster_slots is config.LEAGUE_ROSTER_SLOTS
    assert context.num_teams == 8
    assert context.scoring_provenance == f"configured-{config.YAHOO_LEAGUE_KEY}"
    assert context.unmodeled_scoring == ("bonus_rec_te",)


def test_no_stored_state_reports_the_configured_yahoo_league():
    context = load_league_context(_Store(None), 2024)
    assert context.scoring is config.LEAGUE_SCORING
    assert context.league_key == config.YAHOO_LEAGUE_KEY
    assert context.scoring_provenance == f"configured-{config.YAHOO_LEAGUE_KEY}"


def test_sleeper_with_unmodeled_rules_keeps_its_own_weights_and_reports_them():
    """The live hazard: Sleeper must never be scored with Yahoo's weights."""
    state = _state(
        source="sleeper",
        league_key=config.SLEEPER_LEAGUE_ID,
        scoring_rules=[{"stat_key": "rec", "points": 1.0}],
        unmapped_scoring_rules=[
            {"points": 1.0, "provider_name": "ff"},
            {"points": -1.0, "provider_name": "fgmiss"},
        ],
    )
    context = load_league_context(_Store(state), 2024)
    assert context.scoring.weights == {"rec": 1.0}
    assert context.scoring is not config.LEAGUE_SCORING
    assert context.scoring_complete is False
    assert context.league_key == config.SLEEPER_LEAGUE_KEY
    assert context.scoring_provenance == f"partial-{config.SLEEPER_LEAGUE_KEY}"
    assert context.unmodeled_scoring == ("ff", "fgmiss")


def test_sleeper_with_no_usable_scoring_fails_closed_instead_of_borrowing():
    state = _state(source="sleeper", league_key=config.SLEEPER_LEAGUE_ID, scoring_rules=[])
    with pytest.raises(LeagueSettingsUnavailable, match="no usable scoring rules"):
        load_league_context(_Store(state), 2024)


def test_sleeper_with_no_usable_roster_slots_fails_closed_instead_of_borrowing():
    state = _state(
        source="sleeper",
        league_key=config.SLEEPER_LEAGUE_ID,
        roster_slots=[{"position": "IDP", "count": 1, "is_starting": True}],
    )
    with pytest.raises(LeagueSettingsUnavailable, match="no usable roster slots"):
        load_league_context(_Store(state), 2024)
