"""Pure PPR scoring. No I/O — the deterministic heart of the pipeline."""

from __future__ import annotations

from ffb.config import DEFAULT_PPR, ScoringConfig


def ppr_points(stats: dict[str, float], cfg: ScoringConfig = DEFAULT_PPR) -> float:
    """Fantasy points for a stat line under ``cfg`` (default full PPR).

    Stats not present in ``cfg.weights`` (e.g. ADP fields Sleeper mixes in)
    are ignored; missing stats score zero.
    """
    return cfg.points(stats)
