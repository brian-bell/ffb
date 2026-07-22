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
