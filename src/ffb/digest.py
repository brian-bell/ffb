"""Read-time news digest: attach headlines and LLM flags, never points.

News informs; numbers decide. This module only groups stored headlines and
injury labels for the user roster and unrostered mentions. It does not score,
rank, or recommend a lineup.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ffb.lineup import injury_as_of
from ffb.names import normalize_name

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def name_mentioned(name: str, text: str) -> bool:
    """True when the normalized name appears in ``text`` as a contiguous token run.

    Order and adjacency matter: "Josh Allen" must not match a headline that
    mentions Josh Jacobs and Keenan Allen.
    """
    tokens = normalize_name(name).split()
    if not tokens:
        return False
    words = normalize_name(text).split()
    width = len(tokens)
    return any(words[i : i + width] == tokens for i in range(len(words) - width + 1))


def _headline_text(headline: dict[str, Any]) -> str:
    return " ".join(
        part for part in (headline.get("headline") or "", headline.get("summary") or "") if part
    )


def _copy_headline(headline: dict[str, Any]) -> dict[str, Any]:
    return {
        "native_id": headline.get("native_id"),
        "source": headline.get("source"),
        "headline": headline.get("headline"),
        "summary": headline.get("summary") or "",
        "url": headline.get("url"),
        "published_at": headline.get("published_at"),
        "fetched_at": headline.get("fetched_at"),
    }


def attach_headlines(
    players: list[dict[str, Any]],
    headlines: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Left-join matched mentions, then unique name hits, onto each player."""
    by_key: dict[str, list[str]] = {}
    for mention in mentions:
        if not mention.get("matched") or not mention.get("player_key"):
            continue
        by_key.setdefault(mention["player_key"], []).append(mention["headline_id"])

    attached: list[dict[str, Any]] = []
    for player in players:
        key = player.get("player_key")
        wanted = set(by_key.get(key, ()))
        matched: list[dict[str, Any]] = []
        seen: set[tuple[str | None, str | None]] = set()
        for headline in headlines:
            native_id = headline.get("native_id")
            hit = native_id in wanted or (
                bool(player.get("full_name"))
                and name_mentioned(player["full_name"], _headline_text(headline))
            )
            if not hit:
                continue
            identity = (headline.get("source"), native_id)
            if identity in seen:
                continue
            seen.add(identity)
            matched.append(_copy_headline(headline))
        attached.append({**player, "headlines": matched})
    return attached


def watch_players(
    mentions: list[dict[str, Any]],
    roster_keys: set[str],
    league_keys: set[str],
) -> list[dict[str, Any]]:
    """Unrostered matched mentions — news watch list, not a waiver ranking."""
    watched: dict[str, dict[str, Any]] = {}
    for mention in mentions:
        key = mention.get("player_key")
        if not mention.get("matched") or not key:
            continue
        if key in roster_keys or key in league_keys:
            continue
        watched.setdefault(
            key,
            {
                "player_key": key,
                "full_name": mention.get("full_name"),
                "position": mention.get("position"),
                "team": mention.get("team"),
                "selected_position": None,
                "matched": True,
            },
        )
    return sorted(watched.values(), key=lambda row: (row["full_name"] or "", row["player_key"]))


def apply_flags(
    players: list[dict[str, Any]],
    flags: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy Haiku flags/notes onto players without touching any numeric fields."""
    by_key = {
        row["player_key"]: row
        for row in flags
        if isinstance(row, dict) and isinstance(row.get("player_key"), str)
    }
    updated: list[dict[str, Any]] = []
    for player in players:
        copy = dict(player)
        flag = by_key.get(copy.get("player_key") or "")
        if flag:
            note = flag.get("note")
            copy["flag"] = flag.get("flag") if isinstance(flag.get("flag"), str) else None
            copy["note"] = note.strip() if isinstance(note, str) else None
        else:
            copy.setdefault("flag", None)
            copy.setdefault("note", None)
        updated.append(copy)
    return updated


def parse_haiku_flags(text: str) -> list[dict[str, Any]]:
    """Parse a Haiku JSON array into ``{player_key, flag, note}`` only."""
    cleaned = _FENCE.sub("", (text or "").strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    flags: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = item.get("player_key")
        if not isinstance(key, str) or not key.strip():
            continue
        flag = item.get("flag")
        note = item.get("note")
        flags.append(
            {
                "player_key": key.strip(),
                "flag": flag.strip() if isinstance(flag, str) and flag.strip() else None,
                "note": note.strip() if isinstance(note, str) and note.strip() else None,
            }
        )
    return flags


def parse_sonnet_narrative(text: str) -> str:
    """Return Sonnet prose with optional markdown fences stripped."""
    return _FENCE.sub("", (text or "").strip()).strip()


def _assigned_ids(players: list[dict[str, Any]]) -> set[tuple[str | None, str | None]]:
    assigned: set[tuple[str | None, str | None]] = set()
    for player in players:
        for headline in player.get("headlines") or []:
            assigned.add((headline.get("source"), headline.get("native_id")))
    return assigned


def build_digest(
    *,
    roster: list[dict[str, Any]],
    headlines: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    league_keys: set[str],
    week: int | None,
    team_name: str | None,
) -> dict[str, Any]:
    """Assemble the digest payload. Callers attach LLM flags afterwards."""
    roster_keys = {row["player_key"] for row in roster if row.get("player_key")}
    watch = attach_headlines(
        watch_players(mentions, roster_keys, league_keys),
        headlines,
        mentions,
    )
    roster_rows = attach_headlines(roster, headlines, mentions)
    assigned = _assigned_ids(roster_rows) | _assigned_ids(watch)
    other = [
        _copy_headline(headline)
        for headline in headlines
        if (headline.get("source"), headline.get("native_id")) not in assigned
    ]
    fetched = [headline.get("fetched_at") for headline in headlines if headline.get("fetched_at")]
    return {
        "week": week,
        "team_name": team_name,
        "injury_as_of": injury_as_of(roster_rows),
        "news_as_of": max(fetched) if fetched else None,
        "roster": roster_rows,
        "watch": watch,
        "other_headlines": other,
        "narrative": None,
        "llm": {"haiku": False, "sonnet": False, "error": None},
    }


def digest_players(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Roster + watch rows in display order."""
    return list(report.get("roster") or []) + list(report.get("watch") or [])


MINE = "mine"
FREE_AGENT = "free agent"
UNKNOWN_OWNER = "owner unknown"


def league_owners(
    roster_rows: list[dict[str, Any]],
    teams: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Who rosters each league player: ``mine`` or ``rostered by <team>``.

    Unmatched rows stay in so a name-only headline (RSS) about a rival player
    is still attributed to that rival.
    """
    names = {team["team_key"]: team.get("name") for team in teams}
    user_keys = {team["team_key"] for team in teams if team.get("is_user_team")}
    owners: list[dict[str, Any]] = []
    for row in roster_rows:
        full_name = row.get("full_name") or row.get("name")
        if not full_name:
            continue
        team_key = row.get("team_key")
        owner = (
            MINE
            if team_key in user_keys
            else f"rostered by {names.get(team_key) or 'another team'}"
        )
        owners.append(
            {
                "player_key": row.get("player_key") if row.get("matched") else None,
                "full_name": full_name,
                "position": row.get("position") or row.get("primary_position"),
                "owner": owner,
            }
        )
    return owners


def _report_headlines(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        headline for player in digest_players(report) for headline in player.get("headlines") or []
    ]
    return rows + list(report.get("other_headlines") or [])


def _owners_by_name(owners: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Normalized name -> owner row; a name two owners share maps to unknown."""
    by_name: dict[str, dict[str, Any]] = {}
    for row in owners:
        name = normalize_name(row["full_name"])
        seen = by_name.get(name)
        if seen is None:
            by_name[name] = row
        elif seen["owner"] != row["owner"]:
            by_name[name] = {**seen, "owner": UNKNOWN_OWNER}
    return by_name


def headline_owners(
    report: dict[str, Any],
    mentions: list[dict[str, Any]],
    owners: list[dict[str, Any]],
) -> dict[tuple[str | None, str | None], list[dict[str, Any]]]:
    """Every player each digest headline names, labelled with who rosters them.

    Keyed by ``(source, native_id)``. Players come from ESPN athlete mentions,
    then from league-rostered names found in the text. A resolved mention that
    no league team rosters is a free agent only when every roster row is
    resolved; otherwise absence is unproven. Without league rosters, or when
    two teams roster players who share a name, the owner is unknown, never
    guessed.
    """
    by_key = {row["player_key"]: row for row in owners if row.get("player_key")}
    by_name = _owners_by_name(owners)
    # Only unresolved roster rows fall back to name for a resolved mention.
    unkeyed = _owners_by_name([row for row in owners if not row.get("player_key")])
    # An unresolved roster row could be this player under another name.
    fully_resolved = bool(owners) and all(row.get("player_key") for row in owners)
    unrostered = FREE_AGENT if fully_resolved else UNKNOWN_OWNER
    by_headline: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for mention in mentions:
        identity = (mention.get("source"), mention.get("headline_id"))
        by_headline.setdefault(identity, []).append(mention)

    tagged: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for headline in _report_headlines(report):
        identity = (headline.get("source"), headline.get("native_id"))
        if identity in tagged:
            continue
        named: dict[str, dict[str, Any]] = {}
        for mention in by_headline.get(identity, ()):
            name = mention.get("full_name")
            if not name:
                continue
            key = mention.get("player_key") if mention.get("matched") else None
            known = by_key.get(key) if key else None
            known = known or unkeyed.get(normalize_name(name))
            if known is None:
                known = {
                    "full_name": name,
                    "position": mention.get("position"),
                    "owner": unrostered if key else UNKNOWN_OWNER,
                }
            named.setdefault(normalize_name(known["full_name"]), known)
        text = _headline_text(headline)
        for name, row in by_name.items():
            if name not in named and name_mentioned(row["full_name"], text):
                named[name] = row
        tagged[identity] = [
            {"full_name": row["full_name"], "position": row.get("position"), "owner": row["owner"]}
            for row in sorted(named.values(), key=lambda row: row["full_name"])
        ]
    return tagged
