"""Compute step: turn stored projections into a scored, ranked list.

Pure orchestration over the store and the scoring function — no DB or network
access of its own.
"""

from __future__ import annotations

from typing import Any

from ffb.config import DEFAULT_PPR, ScoringConfig
from ffb.scoring import ppr_points
from ffb.store import Store


def ranked(
    store: Store,
    season: int,
    position: str | None = None,
    limit: int | None = None,
    cfg: ScoringConfig = DEFAULT_PPR,
) -> list[dict[str, Any]]:
    """Return ranked rows: ``rank``, ``full_name``, ``position``, ``team``,
    ``points`` (computed), sorted by points descending.
    """
    rows = store.projection_rows(season=season, position=position)
    scored = [
        {
            "player_id": r["player_id"],
            "full_name": r["full_name"],
            "position": r["position"],
            "team": r["team"],
            "points": round(ppr_points(r["stats"], cfg), 2),
        }
        for r in rows
    ]
    scored.sort(key=lambda r: r["points"], reverse=True)
    if limit is not None:
        scored = scored[:limit]
    for i, row in enumerate(scored, start=1):
        row["rank"] = i
    return scored
