"""Sleeper's full NFL player map, used as the injury-status authority."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ffb.sources.sleeper import USER_AGENT

log = logging.getLogger(__name__)

PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"

_INJURY_STATUS = {
    "Questionable": "QUESTIONABLE",
    "Doubtful": "DOUBTFUL",
    "Out": "OUT",
    "IR": "IR",
    "PUP": "PUP",
}
_ROSTER_STATUS = {
    "Injured Reserve": "IR",
    "Physically Unable to Perform": "PUP",
    "Non Football Injury": "NFI",
}
_NO_SIGNAL_ROSTER_STATUS = {"Active", ""}


def snapshot_key() -> str:
    return "sleeper/players_nfl"


def fetch_players() -> dict[str, Any]:
    """Fetch the unauthenticated full player map. Call at most once per day."""
    log.info("api request provider=sleeper method=GET url=%s", PLAYERS_URL)
    response = httpx.get(PLAYERS_URL, headers={"User-Agent": USER_AGENT}, timeout=60.0)
    response.raise_for_status()
    data = response.json()
    count = len(data) if isinstance(data, dict) else "unknown"
    log.info("api response provider=sleeper status=%s items=%s", response.status_code, count)
    return data


def canonical_status(injury_status: Any, roster_status: Any) -> str | None:
    """Map Sleeper tokens without inferring unsupported injury severity."""
    injury = injury_status.strip() if isinstance(injury_status, str) else ""
    roster = roster_status.strip() if isinstance(roster_status, str) else ""
    if injury:
        return _INJURY_STATUS.get(injury, "UNKNOWN")
    if roster in _ROSTER_STATUS:
        return _ROSTER_STATUS[roster]
    if roster not in _NO_SIGNAL_ROSTER_STATUS:
        return "UNKNOWN"
    return None


def parse_players(raw: Any) -> list[dict[str, Any]]:
    """Parse status rows from the map; malformed records are skipped safely."""
    if not isinstance(raw, dict):
        return []
    rows: list[dict[str, Any]] = []
    for map_id, item in raw.items():
        if not isinstance(item, dict):
            log.debug("skip malformed Sleeper player record: %s", map_id)
            continue
        if not isinstance(map_id, (str, int)) or isinstance(map_id, bool):
            continue
        native_id = str(map_id).strip()
        if not native_id:
            continue
        inner_id = item.get("player_id")
        normalized_inner = (
            str(inner_id).strip()
            if isinstance(inner_id, (str, int)) and not isinstance(inner_id, bool)
            else ""
        )
        if normalized_inner and normalized_inner != native_id:
            log.warning(
                "skip Sleeper player record with conflicting ids map_id=%s player_id=%s",
                native_id,
                normalized_inner,
            )
            continue
        raw_injury = item.get("injury_status")
        raw_roster = item.get("status")
        if raw_injury is not None and not isinstance(raw_injury, str):
            raw_injury = str(raw_injury)
        if raw_roster is not None and not isinstance(raw_roster, str):
            raw_roster = str(raw_roster)
        first_name = item.get("first_name") if isinstance(item.get("first_name"), str) else ""
        last_name = item.get("last_name") if isinstance(item.get("last_name"), str) else ""
        full_name = f"{first_name} {last_name}".strip() or native_id
        rows.append(
            {
                "native_id": native_id,
                "full_name": full_name,
                "position": item.get("position") if isinstance(item.get("position"), str) else None,
                "team": item.get("team") if isinstance(item.get("team"), str) else None,
                "raw_injury_status": raw_injury,
                "raw_roster_status": raw_roster,
                "status": canonical_status(raw_injury, raw_roster),
            }
        )
    return rows
