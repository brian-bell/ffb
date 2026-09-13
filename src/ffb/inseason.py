"""Closed publish envelope for the in-season command center.

Each report command (`lineup`, `digest`, `retro`, `ros`) can ``--publish`` the
exact dict it just rendered. This module builds the versioned envelope the
tracker Worker stores under ``inseason:v1:{season}:{kind}:{week}``; it is pure
so the CLI owns all I/O. Secrets never enter the envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1
KINDS = ("lineup", "digest", "retro", "ros")

# Kind-specific provenance keys. Closed per kind: an envelope with extra or
# missing context keys is a programming error, not a data condition.
CONTEXT_KEYS: dict[str, frozenset[str]] = {
    "lineup": frozenset({"league_synced_at", "projection_sources", "snapshot_generated_at"}),
    "digest": frozenset({"sources"}),
    "retro": frozenset({"actuals_synced_at"}),
    "ros": frozenset({"projection_sources", "playoff_weeks_requested"}),
}


def utc_now() -> str:
    """UTC ISO-8601 with a ``Z`` suffix, the envelope's ``generated_at`` format."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def publish_path(kind: str) -> str:
    """Worker route for one report kind."""
    if kind not in KINDS:
        raise ValueError(f"unknown report kind {kind!r}")
    return f"/api/inseason/{kind}"


def build_envelope(
    kind: str,
    *,
    season: int,
    week: int,
    generated_at: str,
    team_name: str | None,
    context: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Wrap one rendered report dict in the closed publish envelope.

    ``report`` is passed through unchanged. ``week`` is the report week for
    ``lineup`` and ``digest``, the scored week for ``retro``, and the stored
    current week at generation for ``ros``.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown report kind {kind!r}")
    if type(season) is not int or season <= 0:
        raise ValueError("season must be a positive integer")
    if type(week) is not int or week <= 0:
        raise ValueError("week must be a positive integer")
    if not isinstance(generated_at, str) or not generated_at.endswith("Z"):
        raise ValueError("generated_at must be a UTC ISO-8601 string ending in Z")
    if team_name is not None and not isinstance(team_name, str):
        raise ValueError("team_name must be a string or None")
    if not isinstance(report, dict):
        raise ValueError("report must be a dict")
    expected = CONTEXT_KEYS[kind]
    if set(context) != expected:
        missing = sorted(expected - set(context))
        extra = sorted(set(context) - expected)
        raise ValueError(f"{kind} context keys mismatch: missing={missing} extra={extra}")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "season": season,
        "week": week,
        "generated_at": generated_at,
        "team_name": team_name,
        "context": dict(context),
        "report": report,
    }
