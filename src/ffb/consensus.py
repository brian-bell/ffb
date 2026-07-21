"""Consensus compute: average per-source projections into one number per player.

Pure orchestration over the store and scoring — no DB or network of its own,
same shape as ``rankings.py``.

We average **per-source scored points**, not raw stats. Under linear PPR that
equals stat-averaging-then-scoring when sources share a stat schema, but it is
robust when they don't and it yields the per-source columns the CLI shows. Each
row carries ``n`` (source count) so a single-source consensus is transparent.
"""

from __future__ import annotations

from typing import Any

from ffb.config import DEFAULT_PPR, ScoringConfig
from ffb.scoring import ppr_points
from ffb.store import Store


def consensus_rows(
    store: Store,
    season: int,
    position: str | None = None,
    scope: str = "season",
    cfg: ScoringConfig = DEFAULT_PPR,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return consensus rows sorted by consensus points descending.

    ``sources`` restricts which sources contribute (``None`` = every source
    stored). Callers pass the active set so output depends on the request, not on
    which sources a prior run happened to persist.

    Each row: ``rank``, ``player_key``, ``full_name``, ``position``, ``team``,
    ``matched``, ``source_points`` ({source: pts}), ``consensus``, ``n``.
    """
    rows = store.projection_rows(season=season, position=position, source=None, scope=scope)
    allowed = set(sources) if sources is not None else None

    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        if allowed is not None and r["source"] not in allowed:
            continue
        entry = grouped.setdefault(
            r["player_key"],
            {
                "player_key": r["player_key"],
                "full_name": r["full_name"],
                "position": r["position"],
                "team": r["team"],
                "matched": r["matched"],
                "source_points": {},
            },
        )
        entry["source_points"][r["source"]] = round(ppr_points(r["stats"], cfg), 2)

    out: list[dict[str, Any]] = []
    for entry in grouped.values():
        points = list(entry["source_points"].values())
        entry["consensus"] = round(sum(points) / len(points), 2)
        entry["n"] = len(points)
        out.append(entry)

    out.sort(key=lambda e: e["consensus"], reverse=True)
    for i, entry in enumerate(out, start=1):
        entry["rank"] = i
    return out
