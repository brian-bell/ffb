"""nflverse ``ff_playerids`` crosswalk: the canonical identity spine.

Pulled via ``nflreadpy.load_ff_playerids()`` (the maintained successor to
``nfl_data_py``; the latter pins an ancient pandas that won't build on 3.12).
``mfl_id`` is the canonical ``player_key`` every source resolves onto, so
consensus can align Sleeper and ESPN on one player.

Fetch hits nflverse and returns record dicts; parse is pure. Numeric ids arrive
as ints (polars ``Int64``); we stringify them so joins are string-to-string and
never trip over ``12345`` vs ``"12345"``.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

SOURCE = "nflverse"

# Columns we keep from the (wide) ff_playerids table, mapped to our names.
_ID_COLS = ("sleeper_id", "espn_id", "yahoo_id", "gsis_id")

# nflverse labels place-kickers "PK"; Sleeper/ESPN and the league use "K". Matched
# players adopt the crosswalk's canonical position, so without this a matched
# kicker lands under "PK" and `--pos K` (plus alignment with unmatched, source-
# labeled kickers) silently misses it. Other nflverse labels (PN punters, IDP
# positions) are out of league scope and pass through untouched.
_POSITION_NORMALIZE = {"PK": "K"}


def snapshot_key() -> str:
    return "nflverse/ff_playerids"


# Columns pulled from the wide ff_playerids frame into the snapshot.
_FETCH_COLS = ("mfl_id", "sleeper_id", "espn_id", "yahoo_id", "gsis_id", "name", "position", "team")


def fetch_playerids() -> list[dict[str, Any]]:
    """Fetch ff_playerids from nflverse as record dicts. Hits the network.

    The polars frame never escapes this module: we select the columns we use
    and hand back plain ``list[dict]`` so the snapshot cache and parser stay
    source-agnostic.
    """
    import nflreadpy as nfl

    df = nfl.load_ff_playerids()
    cols = [c for c in _FETCH_COLS if c in df.columns]
    return df.select(cols).to_dicts()


def _id_str(value: Any) -> str | None:
    """Normalize an id cell to a clean string, or ``None`` when absent.

    Ints/int-valued floats stringify without a trailing ``.0``; real strings
    (e.g. ``gsis_id`` ``"00-0032764"``) pass through untouched; blanks → None.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    return text or None


def parse_crosswalk(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize ff_playerids records into crosswalk spine rows.

    Rows without an ``mfl_id`` (no canonical key possible) are logged and
    skipped. Never raises on a malformed row.
    """
    rows: list[dict[str, Any]] = []
    for item in raw:
        try:
            player_key = _id_str(item.get("mfl_id"))
            if player_key is None:
                log.debug("skip crosswalk row without mfl_id: %s", item.get("name"))
                continue
            position = item.get("position")
            row: dict[str, Any] = {
                "player_key": player_key,
                "full_name": item.get("name"),
                "position": _POSITION_NORMALIZE.get(position, position),
                "team": item.get("team"),
            }
            for col in _ID_COLS:
                row[col] = _id_str(item.get(col))
            rows.append(row)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skip malformed crosswalk row %s: %s", item.get("mfl_id"), exc)
    return rows
