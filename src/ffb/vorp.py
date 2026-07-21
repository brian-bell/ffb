"""VORP — value over replacement, pure compute (no I/O).

Replacement level must respect the flex slot, so a fixed "RB24 is replacement"
rule won't do. Instead we simulate filling every starting slot in the league
from the consensus pool (§3e): each player, in points order, takes an open
dedicated slot for their position, else an open compatible flex slot, else is
skipped. Once capacity is consumed, the **best remaining unassigned player at a
position** sets that position's replacement level; ``VORP = points −
replacement[pos]`` (negative below the baseline). A position exhausted before
its slots fill gets replacement 0.0.
"""

from __future__ import annotations

from typing import Any

# The flex slot label and the positions it accepts.
FLEX_SLOT = "W/R/T"
FLEX_POSITIONS = frozenset({"RB", "WR", "TE"})
BENCH_SLOT = "BN"


def replacement_levels(
    rows: list[dict[str, Any]],
    roster_slots: dict[str, int],
    num_teams: int,
) -> dict[str, float]:
    """Per-position replacement points from a greedy league-wide starter fill.

    ``rows`` carry ``position`` and ``points``. Ties break by points desc then
    ``player_key`` asc so the fill (and thus the baselines) is deterministic.
    """
    dedicated = {
        pos: num_teams * count
        for pos, count in roster_slots.items()
        if pos not in (BENCH_SLOT, FLEX_SLOT)
    }
    flex_open = num_teams * roster_slots.get(FLEX_SLOT, 0)

    pool = sorted(rows, key=lambda r: (-r["points"], r["player_key"]))
    replacement: dict[str, float] = {}
    for r in pool:
        pos = r["position"]
        if dedicated.get(pos, 0) > 0:
            dedicated[pos] -= 1
        elif pos in FLEX_POSITIONS and flex_open > 0:
            flex_open -= 1
        elif pos not in replacement:
            # First unassigned player at this position = best remaining = baseline.
            replacement[pos] = r["points"]
    return replacement


def attach_vorp(
    rows: list[dict[str, Any]],
    roster_slots: dict[str, int],
    num_teams: int,
) -> list[dict[str, Any]]:
    """Return ``rows`` each with a ``vorp`` field (points over replacement).

    A position never left with a surplus (exhausted before its slots fill) uses a
    0.0 baseline, so its top players still score their full points as VORP.
    """
    repl = replacement_levels(rows, roster_slots, num_teams)
    return [{**r, "vorp": round(r["points"] - repl.get(r["position"], 0.0), 2)} for r in rows]
