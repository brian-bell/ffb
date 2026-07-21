"""ESPN projections source: fetch raw player JSON and parse to normalized rows.

Endpoint (unofficial, no auth, spike-verified 2026-07-21)::

    GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/
        seasons/{season}/players?view=kona_player_info&scoringPeriodId=0
    Header x-fantasy-filter: {"players": {"limit": N,
                              "sortPercOwned": {"sortAsc": false, "sortPriority": 1}}}

The response is a **bare top-level JSON list** of player objects (not wrapped in
``{"players": ...}``). Each player's season projection is the ``stats[]`` entry
with ``statSourceId == 1`` and ``scoringPeriodId == 0``; its ``stats`` is a
``{numeric statId: value}`` map that ``config.ESPN_STAT_MAP`` translates into the
same stat keys Sleeper uses, so ``ppr_points`` scores both sources identically.
ESPN's ``appliedTotal`` is 0 in this view, so we compute points ourselves and
leave ``src_pts_ppr`` unset.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ffb import config

log = logging.getLogger(__name__)

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
USER_AGENT = "ffb/0.1 (personal use)"
# The endpoint returns the whole player universe (~2,900 in 2024, sorted by
# ownership). Keep the cap above that so we never silently drop the low-owned
# tail — otherwise a large --limit would miss players.
DEFAULT_LIMIT = 5000


def snapshot_key(season: int) -> str:
    return f"espn/projections_{season}"


def fetch_projections(season: int, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Fetch raw ESPN player rows for ``season``. Hits the network."""
    import json

    xff = {"players": {"limit": limit, "sortPercOwned": {"sortAsc": False, "sortPriority": 1}}}
    headers = {
        "x-fantasy-filter": json.dumps(xff),
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    url = f"{BASE_URL}/seasons/{season}/players"
    resp = httpx.get(
        url,
        params={"view": "kona_player_info", "scoringPeriodId": 0},
        headers=headers,
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()


def _season_projection(player: dict[str, Any]) -> dict[str, Any] | None:
    """Return the season-projection stat entry for a player, or None."""
    for entry in player.get("stats") or []:
        if entry.get("statSourceId") == 1 and entry.get("scoringPeriodId") == 0:
            return entry
    return None


def _translate_stats(espn_stats: dict[str, Any]) -> dict[str, float]:
    """Map ESPN numeric stat ids to our stat keys; drop unmapped ids."""
    out: dict[str, float] = {}
    for raw_id, value in espn_stats.items():
        key = config.ESPN_STAT_MAP.get(int(raw_id))
        if key is not None and value is not None:
            out[key] = value
    return out


def parse_projections(raw: list[dict[str, Any]], season: int) -> list[dict[str, Any]]:
    """Normalize raw ESPN player rows into records ready for ingest.

    Keeps only players with a season projection carrying stats. Never raises on
    a malformed row — it logs and skips.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for player in raw:
        try:
            native_id = player.get("id")
            proj = _season_projection(player)
            if native_id is None or proj is None:
                continue
            espn_stats = proj.get("stats")
            if not espn_stats:
                log.debug("skip ESPN row with no projection stats: %s", native_id)
                continue
            native_id = str(native_id)
            if native_id in seen:
                continue
            seen.add(native_id)

            rows.append(
                {
                    "native_id": native_id,
                    "full_name": player.get("fullName"),
                    "position": config.ESPN_POSITION_MAP.get(player.get("defaultPositionId")),
                    "team": None,  # ESPN gives numeric proTeamId; identity comes from crosswalk
                    "season": season,
                    "source": "espn",
                    "scope": "season",
                    "stats": _translate_stats(espn_stats),
                    "src_pts_ppr": None,
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skip malformed ESPN row %s: %s", player.get("id"), exc)
    return rows
