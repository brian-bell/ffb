"""Waiver targets: unrostered players ranked against the user's weakest starters.

Pure compute over rest-of-season consensus rows, the user's stored roster, and
the league-wide rostered set. The CLI loads DuckDB; this module never does.
Each unrostered player is compared to the weakest ROS-optimal starter in any
slot they could fill, and only players who beat that starter are listed.
Sleeper trending adds are not ingested yet, so there is no trending boost.

IR, PUP, and NFI free agents are omitted, and the same tags bench a rostered
player for the whole baseline. The injury rows this report already receives
have a status and raw roster status, not a return week, so those players
would otherwise be credited for every remaining week. Out and Doubtful keep
the season-total consensus on both sides of the comparison: that number is
not a week stack, and a one-week tag must not erase the rest of it.
"""

from __future__ import annotations

from typing import Any

from ffb.lineup import (
    attach_injuries,
    attach_weekly_points,
    can_fill,
    is_unavailable,
    optimal_starters,
    starting_slot_counts,
)
from ffb.vorp import FLEX_SLOTS, eligible_positions

CANDIDATES_PER_POSITION = 10

# Canonical statuses from ``sleeper_players.canonical_status``. A direct
# Out/Doubtful token wins over the roster list there, so the raw roster status
# is checked too: a player stored as Out who is actually on IR still misses
# the rest of the season.
_SEASON_LONG_STATUSES = frozenset({"IR", "PUP", "NFI"})
_SEASON_LONG_ROSTER = frozenset(
    {
        "Injured Reserve",
        "Physically Unable to Perform",
        "Non Football Injury",
    }
)


def _season_long_absence(injury: dict[str, Any] | None) -> bool:
    """True when stored status says the player will not play out the season.

    Injury rows carry no return week or date. Without one, IR/PUP/NFI receive
    no rest-of-season credit and never become waiver targets. Out, Doubtful,
    and Questionable are this-week designations and are not absences.
    """
    if not isinstance(injury, dict):
        return False
    status = injury.get("status")
    if isinstance(status, str) and status.strip().upper() in _SEASON_LONG_STATUSES:
        return True
    raw_injury = injury.get("raw_injury_status")
    if isinstance(raw_injury, str) and raw_injury.strip().upper() in _SEASON_LONG_STATUSES:
        return True
    roster = injury.get("raw_roster_status")
    return isinstance(roster, str) and roster.strip() in _SEASON_LONG_ROSTER


def _ros_baseline_injuries(injuries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Injuries that should bench someone for the rest of the season.

    Out and Doubtful are this-week tags. Dropping them here keeps that player's
    season consensus in the starter baseline. A list placement (IR/PUP/NFI)
    still benches them, including when the canonical badge is Out.
    """
    kept: list[dict[str, Any]] = []
    for row in injuries:
        if _season_long_absence(row):
            kept.append(row)
            continue
        status = row.get("status")
        if isinstance(status, str) and status.strip().upper() in {"OUT", "DOUBTFUL"}:
            continue
        kept.append(row)
    return kept


def _baselines(players: list[dict[str, Any]], roster_slots: dict[str, int]) -> list[dict]:
    """ROS-optimal starters per slot; each slot left unfilled is empty or unknown.

    An available rostered player with no ROS projection who could fill an open
    slot makes its baseline unknown (named, ``ros`` None) rather than empty:
    a missing projection does not establish zero value.
    """
    open_counts = dict(starting_slot_counts(roster_slots))
    rows: list[dict[str, Any]] = []
    for starter in optimal_starters(players, roster_slots):
        open_counts[starter["slot"]] -= 1
        rows.append(
            {
                "slot": starter["slot"],
                "name": starter["name"],
                "position": starter["position"],
                "ros": starter["points"],
            }
        )
    unprojected = [
        player
        for player in players
        if player.get("points") is None and player.get("name") and not is_unavailable(player)
    ]
    for slot in sorted(open_counts, key=lambda slot: slot in FLEX_SLOTS):
        for _ in range(open_counts[slot]):
            holder = next((player for player in unprojected if can_fill(player, slot)), None)
            if holder is None:
                rows.append({"slot": slot, "name": None, "position": None, "ros": None})
                continue
            unprojected.remove(holder)
            rows.append(
                {"slot": slot, "name": holder["name"], "position": holder["position"], "ros": None}
            )
    rows.sort(key=lambda row: (row["ros"] is not None, row["ros"] or 0.0, row["slot"]))
    return rows


def _injury_label(injury: dict[str, Any] | None) -> str | None:
    """Canonical status badge for a candidate, or ``None`` when unset."""
    if not isinstance(injury, dict):
        return None
    status = injury.get("status")
    return status if status else None


def _unknown(starter: dict[str, Any]) -> bool:
    """A slot held by a rostered player without a ROS projection."""
    return starter["name"] is not None and starter["ros"] is None


def waiver_report(
    consensus: list[dict[str, Any]],
    *,
    roster: list[dict[str, Any]],
    rostered_keys: set[str],
    roster_slots: dict[str, int],
    injuries: list[dict[str, Any]] | None = None,
    unmatched_rostered: list[str] | None = None,
    eligibility: dict[str, list[str]] | None = None,
    free_agent_keys: set[str] | None = None,
    per_position: int = CANDIDATES_PER_POSITION,
) -> dict[str, Any]:
    """Rank unrostered players by ROS gain over the weakest starter they could replace.

    ``rostered_keys`` holds every canonical key on any team in the league, so
    only true free agents enter the pool. Unmatched consensus rows never do.
    ``unmatched_rostered`` names rostered players with no canonical key: they
    cannot be excluded from the pool, so the report says so instead of guessing.
    ``eligibility`` maps a canonical key to provider slot eligibility beyond its
    position (a QB/TE), so such a player is measured at every slot it can fill.
    IR, PUP, and NFI free agents are not in the pool, so their season consensus
    is not a rest-of-season gain.
    """
    injury_rows = injuries or []
    players = attach_injuries(
        attach_weekly_points(roster, consensus), _ros_baseline_injuries(injury_rows)
    )
    starters = _baselines(players, roster_slots)
    startable = eligible_positions(roster_slots)
    injury_by_key = {
        row["player_key"]: row
        for row in injury_rows
        if row.get("matched") and row.get("player_key")
    }
    pool = [
        row
        for row in consensus
        if row.get("matched")
        and row.get("player_key") not in rostered_keys
        and (free_agent_keys is None or row.get("player_key") in free_agent_keys)
        and row.get("position") in startable
        and row.get("consensus") is not None
        and not _season_long_absence(injury_by_key.get(row["player_key"]))
    ]
    candidates: list[dict[str, Any]] = []
    for row in pool:
        probe = {
            "position": row["position"],
            "eligible_positions": (eligibility or {}).get(row["player_key"], []),
        }
        options = [starter for starter in starters if can_fill(probe, starter["slot"])]
        if not options or any(_unknown(starter) for starter in options):
            continue
        weakest = min(options, key=lambda starter: starter["ros"] or 0.0)
        gain = round(float(row["consensus"]) - float(weakest["ros"] or 0.0), 2)
        if gain <= 0:
            continue
        candidates.append(
            {
                "player_key": row["player_key"],
                "name": row.get("full_name") or row.get("name") or "",
                "position": row["position"],
                "team": row.get("team"),
                "ros": row["consensus"],
                "n": row.get("n", 0),
                "injury": _injury_label(injury_by_key.get(row["player_key"])),
                "replaces": weakest["name"],
                "replaces_slot": weakest["slot"],
                "replaces_ros": weakest["ros"],
                "gain": gain,
            }
        )
    candidates.sort(key=lambda row: (-row["gain"], row["name"]))
    kept: list[dict[str, Any]] = []
    per: dict[str, int] = {}
    for row in candidates:
        if per.get(row["position"], 0) >= per_position:
            continue
        per[row["position"]] = per.get(row["position"], 0) + 1
        kept.append(row)
    for index, row in enumerate(kept, start=1):
        row["rank"] = index
    return {
        "candidates": kept,
        "starters": starters,
        "free_agents": len(pool),
        "unmatched_rostered": sorted(unmatched_rostered or []),
        "trending_available": False,
    }
