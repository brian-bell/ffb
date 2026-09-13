"""Sit/start lineup: optimal weekly starters vs stored ``selected_position``.

Pure compute over roster rows and weekly consensus dicts. The CLI loads league
state, ``scope=week{{N}}`` consensus, and stored Sleeper injuries; this module
never touches DuckDB. Unmatched defenses join projections as
``def:<canonical-team>``. Unmatched skill players are never guessed by name.
Out/Doubtful/IR-list designations are shown and excluded from the optimum.
"""

from __future__ import annotations

from typing import Any

from ffb.identity import canonical_defense_key
from ffb.vorp import BENCH_SLOT, FLEX_SLOTS

NON_STARTING_SLOTS = frozenset({BENCH_SLOT, "IR", "IL"})
CLOSE_CALL_POINTS = 1.5
UNAVAILABLE_STATUSES = frozenset({"OUT", "DOUBTFUL", "IR", "PUP", "NFI"})
INJURY_BADGES = {
    "QUESTIONABLE": "Q",
    "DOUBTFUL": "D",
    "OUT": "OUT",
    "IR": "IR",
    "PUP": "PUP",
    "NFI": "NFI",
    "UNKNOWN": "?",
}
_DEDICATED_ORDER = ("QB", "RB", "WR", "TE", "K", "DEF")


def projection_key(row: dict[str, Any]) -> str | None:
    """Return the weekly-consensus join key for a stored roster row."""
    if row.get("matched") and row.get("player_key"):
        return str(row["player_key"])
    position = row.get("primary_position") or row.get("position")
    team = row.get("nfl_team") if "nfl_team" in row else row.get("team")
    defense = canonical_defense_key(position, team)
    if defense is not None:
        return defense[0]
    return None


def is_starter(selected_position: str | None) -> bool:
    return bool(selected_position) and selected_position not in NON_STARTING_SLOTS


def attach_weekly_points(
    roster_rows: list[dict[str, Any]],
    consensus_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy roster rows and attach weekly ``points`` / ``projection_key``."""
    by_key = {row["player_key"]: row for row in consensus_rows}
    attached: list[dict[str, Any]] = []
    for row in roster_rows:
        key = projection_key(row)
        consensus = by_key.get(key) if key is not None else None
        attached.append(
            {
                **row,
                "name": row.get("full_name") or row.get("name") or "",
                "position": row.get("primary_position") or row.get("position"),
                "team": row.get("nfl_team") if "nfl_team" in row else row.get("team"),
                "projection_key": key,
                "points": None if consensus is None else consensus["consensus"],
                "n": 0 if consensus is None else consensus["n"],
                "source_points": (
                    {} if consensus is None else dict(consensus.get("source_points") or {})
                ),
            }
        )
    return attached


def attach_injuries(
    players: list[dict[str, Any]],
    injury_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Left-join matched Sleeper status onto roster players by canonical key."""
    by_key = {
        row["player_key"]: {"status": row["status"], "fetched_at": row.get("fetched_at")}
        for row in injury_rows
        if row.get("matched") and row.get("status")
    }
    attached: list[dict[str, Any]] = []
    for player in players:
        key = player.get("projection_key") or player.get("player_key")
        attached.append({**player, "injury": by_key.get(key)})
    return attached


def is_unavailable(player: dict[str, Any]) -> bool:
    """True for designations that should not be started (Out/IR/Doubtful/lists)."""
    injury = player.get("injury")
    if not isinstance(injury, dict):
        return False
    return injury.get("status") in UNAVAILABLE_STATUSES


def injury_badge(player: dict[str, Any]) -> str | None:
    """Compact Q/O/D/IR label for a lineup row, or ``None`` when healthy/unknown."""
    injury = player.get("injury")
    if not isinstance(injury, dict):
        return None
    status = injury.get("status")
    if not status:
        return None
    return INJURY_BADGES.get(status, str(status))


def injury_as_of(players: list[dict[str, Any]]) -> str | None:
    """Newest Sleeper snapshot timestamp among attached injury rows."""
    stamps = [
        player["injury"]["fetched_at"]
        for player in players
        if isinstance(player.get("injury"), dict) and player["injury"].get("fetched_at")
    ]
    return max(stamps) if stamps else None


def can_fill(player: dict[str, Any], slot: str) -> bool:
    """True when the player's position or Yahoo eligibility can occupy ``slot``."""
    position = player.get("position")
    eligible = player.get("eligible_positions") or []
    if slot in FLEX_SLOTS:
        return position in FLEX_SLOTS[slot] or slot in eligible
    return position == slot or slot in eligible


def starting_slot_counts(roster_slots: dict[str, int]) -> dict[str, int]:
    return {
        slot: count
        for slot, count in roster_slots.items()
        if count > 0 and slot not in NON_STARTING_SLOTS
    }


def _slot_fill_order(slots: dict[str, int]) -> list[str]:
    dedicated = [slot for slot in _DEDICATED_ORDER if slots.get(slot, 0) > 0]
    extras = sorted(
        slot
        for slot, count in slots.items()
        if count > 0 and slot not in FLEX_SLOTS and slot not in _DEDICATED_ORDER
    )
    flex = sorted(
        (slot for slot in FLEX_SLOTS if slots.get(slot, 0) > 0),
        key=lambda slot: (len(FLEX_SLOTS[slot]), slot),
    )
    return dedicated + extras + flex


def _claim_slot(player: dict[str, Any], open_counts: dict[str, int]) -> str | None:
    position = player.get("position")
    if (
        position
        and open_counts.get(position, 0) > 0
        and can_fill(player, position)
        and position not in FLEX_SLOTS
    ):
        open_counts[position] -= 1
        return position
    for slot in sorted(FLEX_SLOTS, key=lambda value: (len(FLEX_SLOTS[value]), value)):
        if open_counts.get(slot, 0) > 0 and can_fill(player, slot):
            open_counts[slot] -= 1
            return slot
    for slot, count in list(open_counts.items()):
        if count > 0 and slot not in FLEX_SLOTS and slot != position and can_fill(player, slot):
            open_counts[slot] -= 1
            return slot
    return None


def _identity(player: dict[str, Any]) -> str:
    return str(
        player.get("projection_key")
        or player.get("player_key")
        or player.get("yahoo_player_id")
        or player.get("name")
    )


def _display_points(points: float) -> float:
    """One-decimal points — the same granularity the CLI shows."""
    return round(float(points), 1)


def _counted_points(row: dict[str, Any]) -> float:
    """Points that contribute to lineup totals: unavailable starters count as 0."""
    if is_unavailable(row):
        return 0.0
    return float(row["points"] or 0.0)


def _points_sort_key(player: dict[str, Any]) -> tuple[int, float, int, str]:
    points = player.get("points")
    non_starter = 0 if is_starter(player.get("selected_position")) else 1
    name = player.get("name") or ""
    if points is None:
        return (1, 0.0, non_starter, name)
    return (0, -_display_points(points), non_starter, name)


def _row(player: dict[str, Any], slot: str) -> dict[str, Any]:
    row = {
        "slot": slot,
        "name": player.get("name") or "",
        "position": player.get("position"),
        "team": player.get("team"),
        "points": player.get("points"),
        "n": player.get("n", 0),
        "selected_position": player.get("selected_position"),
        "player_key": player.get("player_key"),
        "projection_key": player.get("projection_key"),
        "yahoo_player_id": player.get("yahoo_player_id"),
        "matched": bool(player.get("matched")),
    }
    if player.get("injury") is not None:
        row["injury"] = player["injury"]
    if player.get("undecidable"):
        row["undecidable"] = True
    return row


def _expand_current(players: list[dict[str, Any]], roster_slots: dict[str, int]) -> list[dict]:
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for player in players:
        slot = player.get("selected_position")
        if is_starter(slot):
            by_slot.setdefault(slot, []).append(player)
    for group in by_slot.values():
        group.sort(key=lambda row: (row.get("name") or "", row.get("yahoo_player_id") or ""))
    rows: list[dict[str, Any]] = []
    for slot in _slot_fill_order(starting_slot_counts(roster_slots)):
        for player in by_slot.get(slot, []):
            rows.append(_row(player, slot))
    extras = [
        slot
        for slot in by_slot
        if slot not in starting_slot_counts(roster_slots) and slot not in NON_STARTING_SLOTS
    ]
    for slot in sorted(extras):
        for player in by_slot[slot]:
            rows.append(_row(player, slot))
    return rows


def _assign_optimal(players: list[dict[str, Any]], roster_slots: dict[str, int]) -> list[dict]:
    open_counts = dict(starting_slot_counts(roster_slots))
    assigned: list[tuple[str, dict[str, Any]]] = []
    for player in sorted(players, key=_points_sort_key):
        if player.get("points") is None or is_unavailable(player):
            continue
        slot = _claim_slot(player, open_counts)
        if slot is not None:
            assigned.append((slot, player))
    return _rows_in_slot_order(assigned, roster_slots)


def _rows_in_slot_order(
    assigned: list[tuple[str, dict[str, Any]]], roster_slots: dict[str, int]
) -> list[dict[str, Any]]:
    slot_order = _slot_fill_order(starting_slot_counts(roster_slots))
    order = {slot: index for index, slot in enumerate(slot_order)}
    assigned.sort(key=lambda item: (order.get(item[0], 99), item[1].get("name") or ""))
    return [_row(player, slot) for slot, player in assigned]


def _group_by_slot(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["slot"], []).append(row)
    return grouped


def align_lineup_slots(
    current: list[dict[str, Any]],
    optimal: list[dict[str, Any]],
    roster_slots: dict[str, int],
) -> list[dict[str, Any]]:
    """Pair current vs optimal occupants by slot, padding vacant sides."""
    counts = starting_slot_counts(roster_slots)
    current_by = _group_by_slot(current)
    optimal_by = _group_by_slot(optimal)
    extras = sorted(
        slot
        for slot in set(current_by) | set(optimal_by)
        if slot not in counts and slot not in NON_STARTING_SLOTS
    )
    aligned: list[dict[str, Any]] = []
    for slot in _slot_fill_order(counts) + extras:
        left = current_by.get(slot, [])
        right = optimal_by.get(slot, [])
        for index in range(max(counts.get(slot, 0), len(left), len(right))):
            aligned.append(
                {
                    "slot": slot,
                    "current": left[index] if index < len(left) else None,
                    "optimal": right[index] if index < len(right) else None,
                }
            )
    return aligned


def _retain_unfilled_current(
    current: list[dict[str, Any]],
    optimal: list[dict[str, Any]],
    roster_slots: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep an unprojected starter when their selected slot has no replacement."""
    open_counts = dict(starting_slot_counts(roster_slots))
    for row in optimal:
        if row["slot"] in open_counts:
            open_counts[row["slot"]] -= 1
    optimal_ids = {_identity(row) for row in optimal}
    undecidable: list[dict[str, Any]] = []
    kept = list(optimal)
    for row in current:
        if _identity(row) in optimal_ids or is_unavailable(row):
            continue
        slot = row["slot"]
        if open_counts.get(slot, 0) <= 0:
            continue
        open_counts[slot] -= 1
        retained = {**row, "undecidable": True}
        kept.append(retained)
        undecidable.append(retained)
        optimal_ids.add(_identity(row))
    return (
        _rows_in_slot_order([(row["slot"], row) for row in kept], roster_slots),
        undecidable,
    )


def compare_lineup(
    players: list[dict[str, Any]],
    roster_slots: dict[str, int],
    *,
    close_call: float = CLOSE_CALL_POINTS,
) -> dict[str, Any]:
    """Compare stored starters to a greedy weekly-point assignment."""
    current = _expand_current(players, roster_slots)
    optimal, undecidable = _retain_unfilled_current(
        current, _assign_optimal(players, roster_slots), roster_slots
    )
    current_ids = {_identity(row) for row in current}
    optimal_ids = {_identity(row) for row in optimal}
    start = [row for row in optimal if _identity(row) not in current_ids]
    sit = [row for row in current if _identity(row) not in optimal_ids]
    current_total = round(sum(_counted_points(row) for row in current), 2)
    optimal_total = round(sum(_counted_points(row) for row in optimal), 2)
    missing = [
        _row(player, player.get("selected_position") or "BN")
        for player in players
        if player.get("points") is None
    ]
    close_calls = _close_calls(players, optimal, close_call)
    return {
        "current": current,
        "optimal": optimal,
        "aligned": align_lineup_slots(current, optimal, roster_slots),
        "start": start,
        "sit": sit,
        "undecidable": undecidable,
        "current_total": current_total,
        "optimal_total": optimal_total,
        "delta": round(optimal_total - current_total, 2),
        "missing_projections": missing,
        "close_calls": close_calls,
        "injury_as_of": injury_as_of(players),
    }


def _close_calls(
    players: list[dict[str, Any]],
    optimal: list[dict[str, Any]],
    close_call: float,
) -> list[dict[str, Any]]:
    optimal_ids = {_identity(row) for row in optimal}
    flagged: list[dict[str, Any]] = []
    for player in players:
        if (
            _identity(player) in optimal_ids
            or player.get("points") is None
            or is_unavailable(player)
        ):
            continue
        for starter in optimal:
            if starter["points"] is None or not can_fill(player, starter["slot"]):
                continue
            gap = round(_display_points(starter["points"]) - _display_points(player["points"]), 1)
            if 0 < gap <= close_call:
                flagged.append(
                    {
                        "name": player.get("name") or "",
                        "points": player["points"],
                        "versus": starter["name"],
                        "slot": starter["slot"],
                        "delta": gap,
                    }
                )
                break
    flagged.sort(key=lambda row: (row["delta"], row["name"]))
    return flagged
