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

from ffb import config
from ffb.names import normalize_name

log = logging.getLogger(__name__)

SOURCE = "nflverse"

# Columns we keep from the (wide) ff_playerids table, mapped to our names.
_ID_COLS = ("sleeper_id", "espn_id", "yahoo_id", "gsis_id")

# nflverse labels place-kickers "PK"; Sleeper/ESPN and the league use "K". Matched
# players adopt the crosswalk's canonical position, so without this a matched
# kicker lands under "PK" and `--position K` (plus alignment with unmatched, source-
# labeled kickers) silently misses it. Other nflverse labels (PN punters, IDP
# positions) are out of league scope and pass through untouched.
_POSITION_NORMALIZE = {"PK": "K"}


def snapshot_key() -> str:
    return "nflverse/ff_playerids"


# Columns pulled from the wide ff_playerids frame into the snapshot.
# stats_id, rotowire_id, and sportradar_id are parse-only: stats_id can fill an
# empty yahoo_id, and the other two (with espn_id and gsis_id) can fill a null
# sleeper_id from the Sleeper player map. None of those three is stored.
_FETCH_COLS = (
    "mfl_id",
    "sleeper_id",
    "espn_id",
    "yahoo_id",
    "gsis_id",
    "stats_id",
    "rotowire_id",
    "sportradar_id",
    "name",
    "position",
    "team",
)

# External ids shared by nflverse and Sleeper's player map. A null sleeper_id
# is filled only when every present id agrees on one Sleeper player.
_SLEEPER_LINK_FIELDS = ("rotowire_id", "espn_id", "gsis_id", "sportradar_id")


def fetch_playerids() -> list[dict[str, Any]]:
    """Fetch ff_playerids from nflverse as record dicts. Hits the network.

    The polars frame never escapes this module: we select the columns we use
    and hand back plain ``list[dict]`` so the snapshot cache and parser stay
    source-agnostic.
    """
    import nflreadpy as nfl

    log.info("api request provider=nflverse operation=load_ff_playerids")
    df = nfl.load_ff_playerids()
    cols = [c for c in _FETCH_COLS if c in df.columns]
    rows = df.select(cols).to_dicts()
    log.info("api response provider=nflverse operation=load_ff_playerids items=%s", len(rows))
    return rows


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


def parse_crosswalk(
    raw: list[dict[str, Any]], *, sleeper_players: Any = None
) -> list[dict[str, Any]]:
    """Normalize ff_playerids records into crosswalk spine rows.

    Rows without an ``mfl_id`` (no canonical key possible) are logged and
    skipped. Never raises on a malformed row. Empty ``yahoo_id`` is filled from
    ``stats_id`` only when that ``stats_id`` maps to exactly one canonical and is
    not already present as ``yahoo_id`` on another row.

    When ``sleeper_players`` is Sleeper's ``/players/nfl`` map, a null
    ``sleeper_id`` is filled from a unique external-id link. Existing sleeper
    ids are never replaced. Omit the map and parse stays nflverse-only.
    """
    rows: list[dict[str, Any]] = []
    stats_ids: list[str | None] = []
    link_ids: list[dict[str, str | None]] = []
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
            stats_id = _id_str(item.get("stats_id"))
            rows.append(row)
            stats_ids.append(stats_id)
            link_ids.append({field: _link_id(item.get(field)) for field in _SLEEPER_LINK_FIELDS})
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skip malformed crosswalk row %s: %s", item.get("mfl_id"), exc)
    _fill_yahoo_from_unique_stats_id(rows, stats_ids)
    if sleeper_players is not None:
        index, collided = sleeper_link_index(sleeper_players)
        fill_missing_sleeper_ids(rows, link_ids, index, collided)
    return rows


def _link_id(value: Any) -> str | None:
    """Stringify a link id, treating blank and ``0`` as absent."""
    text = _id_str(value)
    if text is None or text == "0":
        return None
    return text


def sleeper_link_index(
    players: Any,
) -> tuple[dict[tuple[str, str], dict[str, Any]], set[tuple[str, str]]]:
    """Map unique ``(field, external_id)`` pairs, and list the collided ones.

    ``players`` is the raw ``/players/nfl`` object keyed by Sleeper id. The
    set holds ids owned by two Sleeper records. Those are not missing: a
    later fill must reject a row that carries one, even when another id on
    that row is unique. Records whose map key and ``player_id`` disagree are
    skipped.
    """
    if not isinstance(players, dict):
        return {}, set()
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for map_id, item in players.items():
        if not isinstance(item, dict) or isinstance(map_id, bool):
            continue
        if not isinstance(map_id, (str, int)):
            continue
        native_id = str(map_id).strip()
        if not native_id:
            continue
        inner = item.get("player_id")
        if inner is not None and not isinstance(inner, bool):
            inner_text = str(inner).strip()
            if inner_text and inner_text != native_id:
                continue
        first = item.get("first_name") if isinstance(item.get("first_name"), str) else ""
        last = item.get("last_name") if isinstance(item.get("last_name"), str) else ""
        position = item.get("position") if isinstance(item.get("position"), str) else None
        record = {
            "native_id": native_id,
            "full_name": f"{first} {last}".strip(),
            "position": position,
        }
        for field in _SLEEPER_LINK_FIELDS:
            external = _link_id(item.get(field))
            if external is None:
                continue
            buckets.setdefault((field, external), []).append(record)
    index: dict[tuple[str, str], dict[str, Any]] = {}
    collided: set[tuple[str, str]] = set()
    for key, records in buckets.items():
        if len({record["native_id"] for record in records}) == 1:
            index[key] = records[0]
        else:
            collided.add(key)
    return index, collided


def _fantasy_position(position: Any) -> str | None:
    if position == "PK":
        return "K"
    if isinstance(position, str) and position in config.FANTASY_POSITIONS:
        return position
    return None


def fill_missing_sleeper_ids(
    rows: list[dict[str, Any]],
    link_ids: list[dict[str, str | None]],
    index: dict[tuple[str, str], dict[str, Any]],
    collided: set[tuple[str, str]],
) -> int:
    """Fill null ``sleeper_id`` values in place. Returns how many were set.

    A row is filled only when every present link id that Sleeper publishes
    points at the same player, that id is not already on another row, the
    normalized names match, and known fantasy positions agree (``PK`` is ``K``).
    An external id shared by two Sleeper records rejects the row even when
    another id on it is unique. An id Sleeper does not publish is not a
    disagreement. Two rows proposing one id are both left unset. Id-label
    drift outside ``FANTASY_POSITIONS`` (``DE`` versus ``DL``) does not block
    the link.
    """
    claimed = {row["sleeper_id"] for row in rows if row.get("sleeper_id")}
    proposals: dict[str, list[int]] = {}
    for index_row, (row, links) in enumerate(zip(rows, link_ids, strict=True)):
        if row.get("sleeper_id"):
            continue
        name = row.get("full_name") or ""
        if not str(name).strip():
            continue
        chosen: dict[str, Any] | None = None
        conflict = False
        for field in _SLEEPER_LINK_FIELDS:
            external = links.get(field)
            if not external:
                continue
            key = (field, external)
            if key in collided:
                conflict = True
                break
            hit = index.get(key)
            if hit is None:
                continue
            if chosen is None:
                chosen = hit
            elif chosen["native_id"] != hit["native_id"]:
                conflict = True
                break
        if conflict or chosen is None or not chosen.get("full_name"):
            continue
        native_id = str(chosen["native_id"])
        if native_id in claimed:
            continue
        if normalize_name(str(chosen["full_name"])) != normalize_name(str(name)):
            continue
        if not _positions_compatible(row.get("position"), chosen.get("position")):
            continue
        proposals.setdefault(native_id, []).append(index_row)
    filled = 0
    for native_id, indexes in proposals.items():
        if len(indexes) != 1 or native_id in claimed:
            continue
        rows[indexes[0]]["sleeper_id"] = native_id
        claimed.add(native_id)
        filled += 1
    return filled


def _positions_compatible(crosswalk_position: Any, sleeper_position: Any) -> bool:
    left = _fantasy_position(crosswalk_position)
    right = _fantasy_position(sleeper_position)
    if left is None or right is None:
        return True
    return left == right


def _fill_yahoo_from_unique_stats_id(
    rows: list[dict[str, Any]], stats_ids: list[str | None]
) -> None:
    """Copy unique unclaimed ``stats_id`` onto empty ``yahoo_id``.

    Skip when ``stats_id`` is shared across canonicals or already used as
    ``yahoo_id`` on any row, so a fill cannot turn a unique Yahoo match
    ambiguous.
    """
    owners: dict[str, set[str]] = {}
    claimed = {row["yahoo_id"] for row in rows if row["yahoo_id"] is not None}
    for row, stats_id in zip(rows, stats_ids, strict=True):
        if stats_id is None:
            continue
        owners.setdefault(stats_id, set()).add(row["player_key"])
    unique = {stats_id for stats_id, keys in owners.items() if len(keys) == 1} - claimed
    for row, stats_id in zip(rows, stats_ids, strict=True):
        if row["yahoo_id"] is None and stats_id in unique:
            row["yahoo_id"] = stats_id
