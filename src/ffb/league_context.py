"""Resolve stored league settings with safe whole-component fallbacks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ffb import config

_SUPPORTED_STARTERS = {"QB", "RB", "WR", "TE", "W/R/T", "K", "DEF"}


@dataclass(frozen=True)
class LeagueContext:
    scoring: config.ScoringConfig
    roster_slots: dict[str, int]
    num_teams: int
    scoring_provenance: str
    scoring_complete: bool
    roster_complete: bool


def load_league_context(store: Any, season: int) -> LeagueContext:
    """Return persisted settings only where each component is fully usable."""
    state = store.league_context(season)
    if state is None:
        return LeagueContext(
            config.LEAGUE_SCORING,
            config.LEAGUE_ROSTER_SLOTS,
            config.LEAGUE_NUM_TEAMS,
            "placeholder",
            False,
            False,
        )
    mapped = state["scoring_rules"]
    unmapped = state["unmapped_scoring_rules"]
    scoring_complete = (
        bool(mapped)
        and any(_nonzero(rule["points"]) for rule in mapped)
        and not any(_nonzero(rule["points"]) for rule in unmapped)
    )
    slots = state["roster_slots"]
    roster_complete = any(
        slot["is_starting"] and slot["count"] > 0 and slot["position"] in _SUPPORTED_STARTERS
        for slot in slots
    ) and all(
        not slot["is_starting"] or slot["count"] == 0 or slot["position"] in _SUPPORTED_STARTERS
        for slot in slots
    )
    roster = {
        slot["position"]: slot["count"]
        for slot in slots
        if slot["is_starting"] or slot["position"] == "BN"
    }
    return LeagueContext(
        config.ScoringConfig({rule["stat_key"]: rule["points"] for rule in mapped})
        if scoring_complete
        else config.LEAGUE_SCORING,
        roster if roster_complete else config.LEAGUE_ROSTER_SLOTS,
        state["num_teams"] if state["num_teams"] > 0 else config.LEAGUE_NUM_TEAMS,
        f"yahoo-{state['source']}" if scoring_complete else "placeholder",
        scoring_complete,
        roster_complete,
    )


def _nonzero(value: float) -> bool:
    return math.isfinite(value) and value != 0
