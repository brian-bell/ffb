"""Pure PPR scoring. No I/O — the deterministic heart of the pipeline."""

from __future__ import annotations

from ffb.config import DEFAULT_PPR, ScoringConfig


def ppr_points(
    stats: dict[str, float],
    cfg: ScoringConfig = DEFAULT_PPR,
    *,
    position: str | None = None,
) -> float:
    """Fantasy points for a stat line under ``cfg`` (default full PPR).

    Stats not present in ``cfg.weights`` (e.g. ADP fields Sleeper mixes in)
    are ignored; missing stats score zero. ``position`` preserves compatibility
    with stored pre-normalization D/ST return touchdowns without assigning those
    source fields to individual offensive returners.
    """
    if position != "DEF":
        return cfg.points(stats)

    legacy_return_td = sum(float(stats.get(key, 0.0) or 0.0) for key in ("def_kr_td", "pr_td"))
    if not legacy_return_td:
        return cfg.points(stats)

    normalized = dict(stats)
    normalized.pop("def_kr_td", None)
    normalized.pop("pr_td", None)
    normalized["def_ret_td"] = normalized.get("def_ret_td", 0.0) + legacy_return_td
    return cfg.points(normalized)
