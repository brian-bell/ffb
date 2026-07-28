"""Sanity-check our PPR against Sleeper's own pts_ppr on the real snapshot.

This catches stat-key typos in DEFAULT_PPR that unit fixtures might miss. It is
deliberately tolerant: Sleeper's pts_ppr folds in bonus categories (long
rush/rec bonuses, etc.) we don't model in standard PPR, so a minority of players
diverge by design. We assert the bulk match closely, not exact parity.
"""

import json
from pathlib import Path

import pytest

from ffb.scoring import ppr_points
from ffb.sources.sleeper import parse_projections

SNAPSHOT = (
    Path(__file__).resolve().parents[1]
    / "snapshots"
    / "sleeper"
    / "projections_nfl_2024_regular.json"
)


@pytest.fixture(scope="module")
def skill_diffs():
    if not SNAPSHOT.exists():
        pytest.skip("real snapshot not present")
    rows = parse_projections(json.loads(SNAPSHOT.read_text()))
    return [
        abs(ppr_points(r["stats"]) - r["src_pts_ppr"])
        for r in rows
        if r["src_pts_ppr"] and r["position"] in ("QB", "RB", "WR", "TE")
    ]


def test_median_diff_is_negligible(skill_diffs):
    skill_diffs.sort()
    median = skill_diffs[len(skill_diffs) // 2]
    assert median < 0.5, f"median PPR diff {median} — likely a stat-key error"


def test_most_players_match_closely(skill_diffs):
    within_2 = sum(d <= 2.0 for d in skill_diffs) / len(skill_diffs)
    assert within_2 >= 0.90, f"only {within_2:.0%} within 2.0 pts of Sleeper"
