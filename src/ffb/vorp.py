"""VORP — value over replacement, pure compute (no I/O).

Replacement level must respect each flex slot, so a fixed "RB24 is replacement"
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

# Yahoo flex slot labels and the positions each accepts. Restrictive slots are
# claimed before broader ones so a WR does not consume a slot that an RB needs.
FLEX_SLOTS = {
    "W/T": frozenset({"WR", "TE"}),
    "W/R/T": frozenset({"RB", "WR", "TE"}),
}
BENCH_SLOT = "BN"


def eligible_positions(roster_slots: dict[str, int]) -> frozenset[str]:
    """Return player positions accepted by at least one active starter slot."""
    positions = {
        slot
        for slot, count in roster_slots.items()
        if count > 0 and slot != BENCH_SLOT and slot not in FLEX_SLOTS
    }
    for slot, accepted in FLEX_SLOTS.items():
        if roster_slots.get(slot, 0) > 0:
            positions.update(accepted)
    return frozenset(positions)


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
        if pos != BENCH_SLOT and pos not in FLEX_SLOTS
    }
    flex_open = {
        slot: num_teams * roster_slots.get(slot, 0)
        for slot in sorted(FLEX_SLOTS, key=lambda value: (len(FLEX_SLOTS[value]), value))
    }

    pool = sorted(rows, key=lambda r: (-r["points"], r["player_key"]))
    replacement: dict[str, float] = {}
    for r in pool:
        pos = r["position"]
        if dedicated.get(pos, 0) > 0:
            dedicated[pos] -= 1
        elif slot := next(
            (slot for slot, count in flex_open.items() if count > 0 and pos in FLEX_SLOTS[slot]),
            None,
        ):
            flex_open[slot] -= 1
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
