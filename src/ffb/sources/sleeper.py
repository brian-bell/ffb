"""Sleeper projections source: fetch raw JSON and parse to normalized rows.

Endpoint (no auth, verified 2026-07-20)::

    GET https://api.sleeper.com/projections/nfl/{season}
        ?season_type=regular&position[]=RB&...&order_by=pts_ppr

Each row embeds ``player`` (name/pos/team), ``stats`` (raw stat line plus
Sleeper's own ``pts_ppr``), ``player_id`` and ``company``. We keep one company
(``config.SLEEPER_COMPANY``) for deterministic single-source rankings; consensus
across companies/sources arrives in slice 3.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any

import httpx

from ffb import config

log = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.com/projections/nfl"
USER_AGENT = "ffb/0.1 (personal use)"


def snapshot_key(season: int) -> str:
    return f"sleeper/projections_nfl_{season}_regular"


def fetch_projections(
    season: int,
    positions: tuple[str, ...] = config.SLEEPER_POSITIONS,
) -> list[dict[str, Any]]:
    """Fetch raw season projection rows from Sleeper. Hits the network."""
    params: list[tuple[str, str]] = [
        ("season_type", "regular"),
        ("order_by", "pts_ppr"),
    ]
    params += [("position[]", pos) for pos in positions]
    url = f"{BASE_URL}/{season}"
    resp = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def parse_projections(
    raw: list[dict[str, Any]],
    company: str = config.SLEEPER_COMPANY,
    allowed_positions: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize raw rows into records ready for the store.

    Drops rows without a player object or position, rows from companies other
    than ``company``, and rows whose position is outside the standard lineup
    allowlist (``None`` -> ``config.FANTASY_POSITIONS``, resolved at call time)
    — the fetch's ``position[]`` filter is not honored strictly, so parse
    re-checks it. An IDP league opts in by passing a superset (e.g.
    ``set(config.FANTASY_POSITIONS) | {"LB"}``) — there is deliberately no
    "disable filtering" sentinel. Never raises on a malformed row — it logs
    and skips.
    """
    if allowed_positions is None:
        allowed_positions = config.FANTASY_POSITIONS
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped_positions = 0
    for item in raw:
        try:
            if company is not None and item.get("company") != company:
                continue
            player = item.get("player")
            if not player:
                log.debug("skip row with no player: %s", item.get("player_id"))
                continue
            position = player.get("position")
            player_id = item.get("player_id")
            if not position or not player_id:
                log.debug("skip row missing position/id: %s", player_id)
                continue
            if position not in allowed_positions:
                # Filter before the dedup `seen` add so an excluded row can't
                # shadow a later same-id row.
                skipped_positions += 1
                continue
            if player_id in seen:
                log.debug("skip duplicate player_id: %s", player_id)
                continue
            seen.add(player_id)

            stats = item.get("stats") or {}
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            rows.append(
                {
                    "native_id": player_id,
                    "full_name": full_name,
                    "position": position,
                    "team": player.get("team"),
                    "season": int(item.get("season", 0)),
                    "source": "sleeper",
                    "scope": "season",
                    "stats": stats,
                    "src_pts_ppr": stats.get("pts_ppr"),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skip malformed Sleeper row %s: %s", item.get("player_id"), exc)
    if skipped_positions:
        log.debug("skipped %d Sleeper rows outside position allowlist", skipped_positions)
    return rows
