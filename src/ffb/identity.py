"""Pure canonical identity helpers shared by projection and ADP ingest."""

from __future__ import annotations

from ffb import config


def canonical_team(team: str | None) -> str | None:
    """Return a canonical NFL team code, or ``None`` for an unknown team."""
    if not isinstance(team, str):
        return None
    normalized = team.strip().upper()
    normalized = config.TEAM_ALIASES.get(normalized, normalized)
    return normalized if normalized in config.NFL_TEAM_CODES else None


def canonical_defense_key(position: str | None, team: str | None) -> tuple[str, str] | None:
    """Return ``(player_key, team)`` for a valid team-defense identity."""
    if not isinstance(position, str) or position.strip().upper() not in {"DEF", "DST"}:
        return None
    canonical = canonical_team(team)
    if canonical is None:
        return None
    return f"def:{canonical}", canonical


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
