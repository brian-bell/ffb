"""Cheat-sheet board: join consensus ⋈ ADP, add VORP + tiers, rank, serialize.

Pure compute over plain dicts (consensus rows in, ADP rows in) — no DB, no
network, no file writing (the CLI does I/O). Produces the versioned, self-
contained ``board.json`` contract (§3g) plus markdown/CSV renderings.

``board_rows`` left-joins ADP onto consensus by ``player_key`` (a projection
with no ADP keeps ``adp=None``), appends ADP-only rows that have no consensus
counterpart (rankable by ADP with ``points=None``), excludes unmatched rows,
computes VORP over the rows that have points, tiers per position, sorts by VORP
desc (ADP-only rows sink to the bottom by ADP), and stamps
``rank``/``pos_rank``/``adp_rank``.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from ffb import config
from ffb.tiers import assign_tiers
from ffb.vorp import attach_vorp

BOARD_VERSION = 1

# Contract field order (also the CSV column order). Slice 6 pins against this.
_BOARD_FIELDS = (
    "key",
    "name",
    "pos",
    "team",
    "bye",
    "points",
    "n_sources",
    "vorp",
    "tier",
    "rank",
    "pos_rank",
    "adp",
    "adp_rank",
    "adp_high",
    "adp_low",
    "adp_stdev",
    "matched",
)

# Fallbacks when a position isn't configured (unusual position): one tier, no
# overflow. Real league positions are all in config.
_DEFAULT_TIER_COUNT = 1


def board_rows(
    consensus: list[dict[str, Any]],
    adp: list[dict[str, Any]],
    *,
    roster_slots: dict[str, int],
    num_teams: int,
    tier_count: dict[str, int] = config.TIER_COUNT,
    pools: dict[str, int] = config.POSITION_POOL,
) -> list[dict[str, Any]]:
    """Return board rows (§3g shape) sorted by VORP desc, fully ranked."""
    consensus = [row for row in consensus if row["matched"]]
    adp = [row for row in adp if row["matched"]]
    adp_by_key = {r["player_key"]: r for r in adp}

    # Working rows carry vorp/tiers-friendly keys (player_key/position/points).
    working: list[dict[str, Any]] = []
    for c in consensus:
        a = adp_by_key.get(c["player_key"])
        working.append(_working_from_consensus(c, a))
    seen = {c["player_key"] for c in consensus}
    for a in adp:
        if a["player_key"] not in seen:
            working.append(_working_from_adp(a))

    # VORP over rows with points; ADP-only rows get vorp=None.
    scored = [w for w in working if w["points"] is not None]
    unscored = [w for w in working if w["points"] is None]
    scored = attach_vorp(scored, roster_slots, num_teams)
    for w in unscored:
        w["vorp"] = None

    # Tiers per position over the scored rows; ADP-only rows get tier=None.
    _attach_tiers(scored, tier_count, pools)
    for w in unscored:
        w["tier"] = None

    ordered = _sort_board(scored, unscored)
    _stamp_ranks(ordered)
    return [_to_contract(w) for w in ordered]


def _working_from_consensus(c: dict[str, Any], a: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "player_key": c["player_key"],
        "name": c["full_name"],
        "position": c["position"],
        "team": c["team"],
        "points": c["consensus"],
        "n_sources": c["n"],
        "matched": c["matched"],
        "bye": a["bye"] if a else None,
        "adp": a["adp"] if a else None,
        "adp_high": a["adp_high"] if a else None,
        "adp_low": a["adp_low"] if a else None,
        "adp_stdev": a["adp_stdev"] if a else None,
    }


def _working_from_adp(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_key": a["player_key"],
        "name": a["full_name"],
        "position": a["position"],
        "team": a["team"],
        "points": None,
        "n_sources": 0,
        "matched": a["matched"],
        "bye": a["bye"],
        "adp": a["adp"],
        "adp_high": a["adp_high"],
        "adp_low": a["adp_low"],
        "adp_stdev": a["adp_stdev"],
    }


def _attach_tiers(
    scored: list[dict[str, Any]], tier_count: dict[str, int], pools: dict[str, int]
) -> None:
    """Assign tiers per position, in place, over the scored rows."""
    by_pos: dict[str, list[dict[str, Any]]] = {}
    for w in scored:
        by_pos.setdefault(w["position"], []).append(w)
    tiers: dict[str, int] = {}
    for pos, group in by_pos.items():
        k = tier_count.get(pos, _DEFAULT_TIER_COUNT)
        pool = pools.get(pos, len(group))
        for w in assign_tiers(group, tier_count=k, pool_size=pool):
            tiers[w["player_key"]] = w["tier"]
    for w in scored:
        w["tier"] = tiers[w["player_key"]]


def _sort_board(
    scored: list[dict[str, Any]], unscored: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Scored rows by VORP desc (ties: points desc, key asc); ADP-only rows sink
    to the bottom ordered by ADP asc (missing ADP last)."""
    scored_sorted = sorted(
        scored, key=lambda w: (-w["vorp"], -(w["points"] or 0.0), w["player_key"])
    )
    unscored_sorted = sorted(
        unscored,
        key=lambda w: (
            w["adp"] is None,
            w["adp"] if w["adp"] is not None else 0.0,
            w["player_key"],
        ),
    )
    return scored_sorted + unscored_sorted


def _stamp_ranks(ordered: list[dict[str, Any]]) -> None:
    """Stamp rank (board order), pos_rank (within position), adp_rank (by ADP)."""
    for i, w in enumerate(ordered, start=1):
        w["rank"] = i
    pos_seen: dict[str, int] = {}
    for w in ordered:
        pos_seen[w["position"]] = pos_seen.get(w["position"], 0) + 1
        w["pos_rank"] = pos_seen[w["position"]]
    with_adp = sorted(
        (w for w in ordered if w["adp"] is not None),
        key=lambda w: (w["adp"], w["player_key"]),
    )
    for i, w in enumerate(with_adp, start=1):
        w["adp_rank"] = i
    for w in ordered:
        w.setdefault("adp_rank", None)


def _to_contract(w: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": w["player_key"],
        "name": w["name"],
        "pos": w["position"],
        "team": w["team"],
        "bye": w["bye"],
        "points": w["points"],
        "n_sources": w["n_sources"],
        "vorp": w["vorp"],
        "tier": w["tier"],
        "rank": w["rank"],
        "pos_rank": w["pos_rank"],
        "adp": w["adp"],
        "adp_rank": w["adp_rank"],
        "adp_high": w["adp_high"],
        "adp_low": w["adp_low"],
        "adp_stdev": w["adp_stdev"],
        "matched": w["matched"],
    }


def to_board_json(
    rows: list[dict[str, Any]],
    *,
    season: int,
    num_teams: int,
    roster_slots: dict[str, int],
    generated_at: str,
    scoring: str = "league",
) -> dict[str, Any]:
    """Wrap board rows in the versioned, self-contained contract envelope (§3g).

    ``generated_at`` is passed in (not stamped here) so the module stays
    deterministic under test.
    """
    return {
        "version": BOARD_VERSION,
        "season": season,
        "generated_at": generated_at,
        "scoring": scoring,
        "num_teams": num_teams,
        "roster_slots": roster_slots,
        "players": rows,
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def to_markdown(rows: list[dict[str, Any]], *, season: int) -> str:
    """Per-position markdown sections with a tier break between tiers."""
    lines = [f"# {season} draft cheat sheet", ""]
    by_pos: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_pos.setdefault(r["pos"], []).append(r)
    # A player whose position didn't map sorts last under a placeholder header.
    for pos in sorted(by_pos, key=lambda p: (p is None, p or "")):
        lines.append(f"## {pos or 'Unknown'}")
        lines.append("")
        lines.append("| Rank | Tier | Player | Team | Bye | Proj | VORP | ADP |")
        lines.append("|---:|---:|---|:--:|---:|---:|---:|---:|")
        prev_tier: Any = None
        for r in by_pos[pos]:
            if prev_tier is not None and r["tier"] != prev_tier:
                lines.append("| | | | | | | | |")  # tier break separator
            prev_tier = r["tier"]
            lines.append(
                "| {rank} | {tier} | {name} | {team} | {bye} | {proj} | {vorp} | {adp} |".format(
                    rank=r["pos_rank"],
                    tier=_fmt(r["tier"]),
                    name=r["name"],
                    team=_fmt(r["team"]),
                    bye=_fmt(r["bye"]),
                    proj=_fmt(r["points"]),
                    vorp=_fmt(r["vorp"]),
                    adp=_fmt(r["adp"]),
                )
            )
        lines.append("")
    return "\n".join(lines)


# Spreadsheet formula triggers. Player name/team strings come from an
# unauthenticated external source (FFC), so a leading one of these would execute
# as a formula when the CSV is opened in Excel/Sheets — prefix it with an
# apostrophe. board.json (the machine contract) is unaffected; the CSV is the
# human/spreadsheet rendering.
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value:
        first = value[0]
        # A leading formula trigger executes as a formula; a leading whitespace or
        # control char (tab/CR/LF) sneaks one past a naive check, so escape those too.
        if first in _FORMULA_PREFIXES or first.isspace() or ord(first) < 0x20:
            return "'" + value
    return value


def to_csv(rows: list[dict[str, Any]]) -> str:
    """Flat CSV, one row per player, stable column order (``_BOARD_FIELDS``)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_BOARD_FIELDS))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r[k] is None else _csv_safe(r[k])) for k in _BOARD_FIELDS})
    return buf.getvalue()
