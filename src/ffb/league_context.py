"""Resolve stored league settings without ever borrowing another league's rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ffb import config

_SUPPORTED_STARTERS = {"QB", "RB", "WR", "TE", "W/T", "W/R/T", "K", "DEF"}


class LeagueSettingsUnavailable(ValueError):
    """A league's own settings are unusable and no other league's may stand in."""


@dataclass(frozen=True)
class LeagueContext:
    scoring: config.ScoringConfig
    roster_slots: dict[str, int]
    num_teams: int
    scoring_provenance: str
    scoring_complete: bool
    roster_complete: bool
    league_key: str = config.YAHOO_LEAGUE_KEY
    #: Provider stat names this league scores that the pipeline does not model.
    unmodeled_scoring: tuple[str, ...] = ()


def roster_slot_counts(slots: list[dict[str, Any]]) -> dict[str, int]:
    """Startable slot counts (plus BN) from a bundle's ``settings.roster_slots``."""
    return {
        slot["position"]: slot["count"]
        for slot in slots
        if slot["is_starting"] or slot["position"] == "BN"
    }


def scoring_from_rules(rules: list[dict[str, Any]]) -> config.ScoringConfig:
    """Weights from a bundle's ``settings.scoring_rules``; no cross-league fallback."""
    return config.ScoringConfig({rule["stat_key"]: rule["points"] for rule in rules})


def namespaced_league_key(state: dict[str, Any]) -> str:
    """``provider:league_key`` for the stored league, matching ``config``'s vocabulary."""
    return config.namespaced_league_key(
        str(state.get("source") or ""), str(state.get("league_key") or "")
    )


def load_league_context(store: Any, season: int, league_key: str | None = None) -> LeagueContext:
    """Return a league's own settings, falling back only within that same league.

    The configured ``config.LEAGUE_*`` values describe Brian's Yahoo league. They
    are a safe stand-in for an incomplete Yahoo (or Yahoo-shaped fixture) league
    and nothing else: applying them to a Sleeper league would score it with
    another league's weights. So for any other provider this fails closed rather
    than borrowing, and uses the league's own partial rules when it has usable
    ones.
    """
    state = store.league_context(season, league_key)
    if state is None:
        # Nothing synced yet: the configured Yahoo league is the only league
        # there is, so this is that league's own settings, not a borrow.
        return LeagueContext(
            config.LEAGUE_SCORING,
            config.LEAGUE_ROSTER_SLOTS,
            config.LEAGUE_NUM_TEAMS,
            f"configured-{config.YAHOO_LEAGUE_KEY}",
            False,
            False,
            config.YAHOO_LEAGUE_KEY,
            (),
        )

    league_key = namespaced_league_key(state)
    may_use_configured = state["source"] in config.YAHOO_BUNDLE_SOURCES

    mapped = state["scoring_rules"]
    unmapped = state["unmapped_scoring_rules"]
    unmodeled = tuple(
        sorted(
            str(rule.get("provider_name") or rule.get("provider_stat_id") or "")
            for rule in unmapped
            if _nonzero(rule["points"])
        )
    )
    has_own_weights = bool(mapped) and any(_nonzero(rule["points"]) for rule in mapped)
    scoring_complete = has_own_weights and not unmodeled

    if scoring_complete:
        scoring = scoring_from_rules(mapped)
        provenance = f"synced-{league_key}"
    elif has_own_weights and not may_use_configured:
        # Partial, but they are this league's own weights. Better than another
        # league's, and the unmodeled rules are reported alongside.
        scoring = scoring_from_rules(mapped)
        provenance = f"partial-{league_key}"
    elif may_use_configured:
        scoring = config.LEAGUE_SCORING
        provenance = f"configured-{config.YAHOO_LEAGUE_KEY}"
    else:
        raise LeagueSettingsUnavailable(
            f"league {league_key} has no usable scoring rules; refusing to score it "
            f"with {config.YAHOO_LEAGUE_KEY}'s configured weights. Re-sync the league."
        )

    slots = state["roster_slots"]
    roster_complete = any(
        slot["is_starting"] and slot["count"] > 0 and slot["position"] in _SUPPORTED_STARTERS
        for slot in slots
    ) and all(
        not slot["is_starting"] or slot["count"] == 0 or slot["position"] in _SUPPORTED_STARTERS
        for slot in slots
    )
    if roster_complete:
        roster = roster_slot_counts(slots)
    elif may_use_configured:
        roster = config.LEAGUE_ROSTER_SLOTS
    else:
        raise LeagueSettingsUnavailable(
            f"league {league_key} has no usable roster slots; refusing to build its "
            f"lineup from {config.YAHOO_LEAGUE_KEY}'s configured slots. Re-sync the league."
        )

    return LeagueContext(
        scoring,
        roster,
        state["num_teams"] if state["num_teams"] > 0 else config.LEAGUE_NUM_TEAMS,
        provenance,
        scoring_complete,
        roster_complete,
        league_key,
        unmodeled,
    )


def _nonzero(value: float) -> bool:
    return math.isfinite(value) and value != 0
