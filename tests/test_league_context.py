"""Stored league settings safely override only complete components."""

from ffb import config
from ffb.league_context import load_league_context


class _Store:
    def __init__(self, value):
        self.value = value

    def league_context(self, season):
        return self.value


def test_complete_fixture_context_supplies_scoring_roster_and_team_count():
    state = {
        "scoring_rules": [{"stat_key": "pass_yd", "points": 1.0}],
        "unmapped_scoring_rules": [],
        "roster_slots": [
            {"position": "QB", "count": 1, "is_starting": True},
            {"position": "IR", "count": 2, "is_starting": False},
            {"position": "BN", "count": 5, "is_starting": False},
        ],
        "num_teams": 10,
        "source": "fixture",
    }
    context = load_league_context(_Store(state), 2024)
    assert context.scoring.weights == {"pass_yd": 1.0}
    assert context.roster_slots == {"QB": 1, "BN": 5}
    assert context.num_teams == 10
    assert context.scoring_provenance == "yahoo-fixture"


def test_incomplete_components_fall_back_independently():
    state = {
        "scoring_rules": [],
        "unmapped_scoring_rules": [{"points": 1.0}],
        "roster_slots": [{"position": "IDP", "count": 1, "is_starting": True}],
        "num_teams": 8,
        "source": "fixture",
    }
    context = load_league_context(_Store(state), 2024)
    assert context.scoring is config.LEAGUE_SCORING
    assert context.roster_slots is config.LEAGUE_ROSTER_SLOTS
    assert context.num_teams == 8
    assert context.scoring_provenance == "placeholder"
