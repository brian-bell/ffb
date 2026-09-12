"""Thin Anthropic Messages client for digest flags and narrative.

Numbers stay in pure compute. This module only asks Claude for prose/flags from
already-selected headlines and injury labels. Tests inject ``complete``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-5"
ANTHROPIC_VERSION = "2023-06-01"


def api_key_from_env(env: dict[str, str] | None = None) -> str | None:
    """Prefer ``FFB_ANTHROPIC_API_KEY``, then ``ANTHROPIC_API_KEY``."""
    source = env if env is not None else os.environ
    for name in ("FFB_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        value = source.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def complete_claude(
    *,
    model: str,
    system: str,
    user: str,
    api_key: str,
    timeout: float = 45.0,
) -> str:
    """Send one Messages request and return concatenated text blocks."""
    response = httpx.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    blocks = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def haiku_system_prompt() -> str:
    return (
        "You flag fantasy-football news. Use only the provided headlines and "
        "injury labels. Return a JSON array of objects with keys player_key, "
        "flag, and note. Flags are short tokens such as Q, OUT, IR, or WATCH. "
        "Never invent statistics, projections, rankings, or point totals. "
        "Never tell the user who to start based on numbers."
    )


def sonnet_system_prompt() -> str:
    return (
        "Write a short Tuesday-brief narrative from the provided roster and "
        "watch-list headlines. News informs; numbers decide. Do not invent "
        "statistics, projections, rankings, or point totals. Do not change "
        "any numeric recommendation. Two or three sentences."
    )


def haiku_user_prompt(report: dict[str, Any]) -> str:
    """Serialize players and headlines for Haiku flagging."""
    lines = ["Players and headlines:"]
    for section in ("roster", "watch"):
        lines.append(f"[{section}]")
        for player in report.get(section) or []:
            injury = player.get("injury") or {}
            status = injury.get("status") if isinstance(injury, dict) else None
            lines.append(
                f"- {player.get('player_key')} | {player.get('full_name')} | "
                f"{player.get('position')} | injury={status or 'none'}"
            )
            for headline in player.get("headlines") or []:
                lines.append(f"  * {headline.get('headline')}: {headline.get('summary')}")
    return "\n".join(lines)


def sonnet_user_prompt(report: dict[str, Any]) -> str:
    """Serialize flagged players for the Sonnet narrative."""
    lines = [f"Week {report.get('week')} digest for {report.get('team_name') or 'the user'}."]
    for section in ("roster", "watch"):
        lines.append(f"[{section}]")
        for player in report.get(section) or []:
            lines.append(
                f"- {player.get('full_name')} ({player.get('position')}) "
                f"flag={player.get('flag') or 'none'} note={player.get('note') or ''}"
            )
            for headline in player.get("headlines") or []:
                lines.append(f"  * {headline.get('headline')}")
    if report.get("other_headlines"):
        lines.append("[other]")
        for headline in report["other_headlines"]:
            lines.append(f"- {headline.get('headline')}")
    return "\n".join(lines)
