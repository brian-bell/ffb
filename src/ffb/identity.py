"""Pure canonical identity helpers shared by projection and ADP ingest."""

from __future__ import annotations

import re

from ffb import config

_DEFENSE_POSITIONS = {"DEF", "DST", "D/ST", "D/S/T"}
_DEFENSE_NAME_SUFFIXES = {"DEFENSE", "DST", "DEF"}
_NON_ALNUM = re.compile(r"[^\w\s]+")


def _normalize_team_label(team: str) -> str:
    """Uppercase a team label and drop trailing D/ST / Defense tokens."""
    normalized = team.strip().upper().replace("D/ST", "DST").replace("D / ST", "DST")
    tokens = _NON_ALNUM.sub(" ", normalized).split()
    while len(tokens) > 1 and tokens[-1] in _DEFENSE_NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def canonical_team(team: str | None) -> str | None:
    """Return a canonical NFL team code, or ``None`` for an unknown team."""
    if not isinstance(team, str):
        return None
    normalized = _normalize_team_label(team)
    if not normalized:
        return None
    normalized = config.TEAM_ALIASES.get(normalized, normalized)
    return normalized if normalized in config.NFL_TEAM_CODES else None


def is_defense_position(position: str | None) -> bool:
    """True for the team-defense labels providers actually emit."""
    return isinstance(position, str) and position.strip().upper() in _DEFENSE_POSITIONS


def canonical_defense_key(position: str | None, team: str | None) -> tuple[str, str] | None:
    """Return ``(player_key, team)`` for a valid team-defense identity."""
    if not is_defense_position(position):
        return None
    canonical = canonical_team(team)
    if canonical is None:
        return None
    return f"def:{canonical}", canonical


def defense_identity(position: str | None, *candidates: object) -> tuple[str, str] | None:
    """Resolve a defense from the first candidate that canonicalizes.

    Callers pass team codes, then display names (``Rams``, ``Denver Broncos``)
    so a missing editorial abbreviation still matches.
    """
    if not is_defense_position(position):
        return None
    for candidate in candidates:
        if isinstance(candidate, str):
            hit = canonical_defense_key("DEF", candidate)
            if hit is not None:
                return hit
    return None


#: Slots a fantasy position can fill, beyond its own dedicated slot.
_FLEX_POSITIONS = {"RB", "WR", "TE"}


def slot_eligibility(position: str) -> list[str]:
    """Lineup slots a position can fill: its own, plus the RB/WR/TE flex."""
    if position in _FLEX_POSITIONS:
        return [position, "W/R/T"]
    return [position]


def merge_eligibility(
    position: str, provider_position: object, provider_slots: object
) -> list[str]:
    """Slots for ``position``, plus provider slots beyond its own primary position.

    The crosswalk position is authoritative: its slots always come first and are
    always present, and a provider's stale primary position can never add a slot
    of its own. A WR-tagged RB is therefore an RB, never eligible at WR.

    What does survive is genuine multi-position eligibility — a QB/TE is listed
    by the provider at both, and the second position is information the
    crosswalk's single position cannot express. Those extras are slots the
    provider reported that its own primary position does not already imply.
    """
    slots = slot_eligibility(position)
    baseline = (
        set(slot_eligibility(provider_position)) if isinstance(provider_position, str) else set()
    )
    if not isinstance(provider_slots, list):
        return slots
    for slot in provider_slots:
        if isinstance(slot, str) and slot and slot not in baseline and slot not in slots:
            slots.append(slot)
    return slots
