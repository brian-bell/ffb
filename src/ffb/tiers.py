"""Positional tiers via largest-gap splitting, pure compute (no I/O).

Per position, over players sorted by points desc (§3f): restrict to a draftable
``pool_size``; split the pool at the ``tier_count − 1`` largest point drops
between consecutive players; players beyond the pool share one overflow tier
(``tier_count + 1``). Equal-gap ties break at the earlier index so output is
deterministic. Zero dependencies — the boundaries land where a human would draw
them ("big drop after player N"); k-means/Jenks buys near-identical output on
12-point-scale data for a dependency or 50 lines of DP.
"""

from __future__ import annotations

from typing import Any


def assign_tiers(
    rows: list[dict[str, Any]],
    tier_count: int,
    pool_size: int,
) -> list[dict[str, Any]]:
    """Return ``rows`` (one position) sorted by points desc, each with a ``tier``.

    ``rows`` carry ``points`` and ``player_key`` (the latter breaks point ties so
    ordering is stable).
    """
    ordered = sorted(rows, key=lambda r: (-r["points"], r["player_key"]))
    pool = ordered[:pool_size]

    # Gaps between consecutive pool members: (drop, index-of-earlier-player).
    gaps = [(pool[i]["points"] - pool[i + 1]["points"], i) for i in range(len(pool) - 1)]
    # The tier_count-1 largest drops are boundaries; ties prefer the earlier index.
    n_boundaries = min(max(tier_count - 1, 0), len(gaps))
    boundaries = {idx for _, idx in sorted(gaps, key=lambda g: (-g[0], g[1]))[:n_boundaries]}

    overflow_tier = tier_count + 1
    out: list[dict[str, Any]] = []
    tier = 1
    for j, row in enumerate(ordered):
        if j >= pool_size:
            out.append({**row, "tier": overflow_tier})
            continue
        if j > 0 and (j - 1) in boundaries:
            tier += 1
        out.append({**row, "tier": tier})
    return out
