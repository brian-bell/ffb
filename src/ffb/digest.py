"""Read-time news digest: attach headlines and LLM flags, never points.

News informs; numbers decide. This module only groups stored headlines and
injury labels for the user roster and unrostered mentions. It does not score,
rank, or recommend a lineup.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ffb.lineup import attach_injuries, injury_as_of
from ffb.names import normalize_name

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def name_mentioned(name: str, text: str) -> bool:
    """True when every normalized name token appears as a token in ``text``."""
    tokens = normalize_name(name).split()
    if not tokens:
        return False
    haystack = set(normalize_name(text).split())
    return all(token in haystack for token in tokens)


def _headline_text(headline: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (headline.get("headline") or "", headline.get("summary") or "")
        if part
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
