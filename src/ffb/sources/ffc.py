"""Fantasy Football Calculator ADP source: fetch raw JSON + pure parse.

Endpoint (free, no auth, spike-verified 2026-07-21)::

    GET https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={N}&year={season}

Response shape::

    {"status": "Success",
     "meta": {"type", "teams", "rounds", "total_drafts", "start_date", "end_date"},
     "players": [{"player_id", "name", "position", "team", "adp", "adp_formatted",
                  "times_drafted", "high", "low", "stdev", "bye"}]}

``player_id`` is FFC-internal (no column in ``ff_playerids``), so ingest resolves
each row to a canonical ``player_key`` by **name + position** (see ``names.py``),
not by id. Positions are normalized here (``PK -> K``) and team codes aliased to
nflverse/MFL style (``SF -> SFO``) so the downstream name matcher's team tiebreak
compares like with like. ``parse_adp`` is pure and never raises on a bad row.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ffb import config

log = logging.getLogger(__name__)

BASE_URL = "https://fantasyfootballcalculator.com/api/v1/adp"
USER_AGENT = "ffb/0.1 (personal use)"


def snapshot_key(
    season: int,
    teams: int = config.LEAGUE_NUM_TEAMS,
    fmt: str = config.FFC_FORMAT,
) -> str:
    return f"ffc/adp_{fmt}_{teams}_{season}"


def fetch_adp(
    season: int,
    teams: int = config.LEAGUE_NUM_TEAMS,
    fmt: str = config.FFC_FORMAT,
) -> dict[str, Any]:
    """Fetch raw ADP for ``season`` from FFC. Hits the network."""
    url = f"{BASE_URL}/{fmt}"
    resp = httpx.get(
        url,
        params={"teams": teams, "year": season},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_adp(raw: dict[str, Any], season: int) -> list[dict[str, Any]]:
    """Normalize a raw FFC response into rows shaped for ingest (minus
    ``player_key``/``matched``, which name-resolution attaches).

    Returns ``[]`` when the pull isn't a success (so the snapshot ``is_valid``
    gate treats a bad refresh as empty). A malformed player row logs and is
    skipped, never raised.
    """
    if not isinstance(raw, dict) or raw.get("status") != "Success":
        status = raw.get("status") if isinstance(raw, dict) else None
        log.warning("FFC pull not successful (status=%r); no ADP rows", status)
        return []

    rows: list[dict[str, Any]] = []
    for player in raw.get("players") or []:
        if not isinstance(player, dict):
            # A successful pull can still carry a junk entry (null/string); skip
            # it rather than let `.get` raise (the is_valid refresh gate relies on
            # this never raising).
            log.warning("skip non-object FFC player entry: %r", player)
            continue
        try:
            native_id = player.get("player_id")
            name = player.get("name")
            position = player.get("position")
            # name/position must be non-empty strings: a non-string value would
            # crash name-resolution downstream (unicodedata.normalize / dict key),
            # and there's no per-row guard there, so it'd sink the whole ingest.
            if (
                native_id is None
                or not isinstance(name, str)
                or not isinstance(position, str)
                or not name
                or not position
            ):
                log.warning("skip FFC row with missing/invalid id/name/position: %s", player)
                continue
            position = config.FFC_POSITION_MAP.get(position, position)
            team = player.get("team")
            team = config.FFC_TEAM_ALIASES.get(team, team)
            rows.append(
                {
                    "native_id": str(native_id),
                    "full_name": name,
                    "position": position,
                    "team": team,
                    "bye": _int_or_none(player.get("bye")),
                    "adp": _float_or_none(player.get("adp")),
                    "adp_high": _int_or_none(player.get("high")),
                    "adp_low": _int_or_none(player.get("low")),
                    "adp_stdev": _float_or_none(player.get("stdev")),
                    "times_drafted": _int_or_none(player.get("times_drafted")),
                    "season": season,
                    "source": "ffc",
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skip malformed FFC row %s: %s", player.get("player_id"), exc)
    return rows
