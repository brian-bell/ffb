"""Name-based crosswalk matching for sources with no id in ``ff_playerids``.

FFC's ADP uses an FFC-internal ``player_id`` with no column in the nflverse
crosswalk, so ``store.resolve_batch`` (id-keyed) can't apply. Instead we resolve
an ADP row to a canonical ``player_key`` by **(normalized name, position)** with
a team tiebreak. Pure functions, heavily unit-tested — a wrong merge silently
corrupts the board, so ambiguity always resolves to *unmatched*, never a guess.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Trailing generational suffixes to drop (Roman numerals + jr/sr). Sources
# disagree on whether they carry these, so they must never block a match.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Normalize a display name for matching: lowercase, strip punctuation and
    diacritics, drop generational suffixes, collapse whitespace."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # Drop punctuation (apostrophes, hyphens, periods) entirely so "Ja'Marr" and
    # "Amon-Ra" collapse to solid tokens; keep only word chars and whitespace.
    text = re.sub(r"[^\w\s]", "", text)
    tokens = text.split()
    # Strip trailing suffix tokens (never reduce the name to nothing).
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def build_name_index(
    crosswalk_rows: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Index crosswalk rows by ``(normalized name, position)`` for name matching.

    A given ``(name, position)`` can have several candidates (the crosswalk holds
    ~551 duplicate pairs, overwhelmingly retired players on team ``FA``); the
    matcher disambiguates, so the index just collects every candidate.
    """
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in crosswalk_rows:
        name = row.get("full_name")
        position = row.get("position")
        if not name or not position:
            continue
        index.setdefault((normalize_name(name), position), []).append(row)
    return index


def match_by_name(
    index: dict[tuple[str, str], list[dict[str, Any]]],
    name: str,
    position: str,
    *,
    team: str | None = None,
) -> str | None:
    """Resolve ``(name, position)`` to a canonical ``player_key`` via the index.

    Ambiguity resolves to ``None`` (unmatched), never a guess: a wrong merge
    silently corrupts the board, an unmatched row is visible and rankable.
    """
    candidates = index.get((normalize_name(name), position))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]["player_key"]
    # Multiple candidates: drop free-agent (retired) rows first — the crosswalk's
    # duplicate (name, pos) pairs are overwhelmingly retired players on "FA".
    active = [c for c in candidates if (c.get("team") or "").upper() != "FA"]
    if len(active) == 1:
        return active[0]["player_key"]
    # Still ambiguous: a team match uniquely picks one, else unmatched.
    if team is not None:
        matches = [c for c in active if c.get("team") == team]
        if len(matches) == 1:
            return matches[0]["player_key"]
    return None
