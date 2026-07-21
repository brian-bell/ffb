"""Static configuration: paths, season, scoring constants.

These are deliberately plain module-level values for the walking skeleton.
Slice 4 will replace ``DEFAULT_PPR`` with scoring pulled from Yahoo league
settings, so keep scoring here (not hard-coded at call sites).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Repo root = two levels up from this file (src/ffb/config.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SNAPSHOT_DIR = REPO_ROOT / "snapshots"
DB_PATH = DATA_DIR / "ffb.duckdb"

# Season we build against. 2024 is complete and stable; flip to the live
# season via the CLI --season flag once Sleeper publishes it.
DEFAULT_SEASON = 2024

# Sleeper returns multiple projection companies per player; pin one for
# determinism in the walking skeleton. Consensus across companies/sources
# arrives in slice 3.
SLEEPER_COMPANY = "rotowire"

SLEEPER_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

# --- ESPN projections (spike-verified 2026-07-21) ---------------------------
# The /players endpoint reports stats as {numeric statId: value}. This maps the
# ids we score to the same stat keys Sleeper uses, so ppr_points scores both
# sources identically. Verified against known players; covers every DEFAULT_PPR
# weight. Unmapped ids (attempts, targets, etc.) are ignored at parse time.
ESPN_STAT_MAP = {
    3: "pass_yd",
    4: "pass_td",
    19: "pass_2pt",
    20: "pass_int",
    24: "rush_yd",
    25: "rush_td",
    26: "rush_2pt",
    42: "rec_yd",
    43: "rec_td",
    44: "rec_2pt",
    53: "rec",
    72: "fum_lost",
}

# ESPN defaultPositionId -> our position label.
ESPN_POSITION_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}


@dataclass(frozen=True)
class ScoringConfig:
    """Per-stat point values. Missing stats score zero."""

    weights: dict[str, float] = field(default_factory=dict)

    def points(self, stats: dict[str, float]) -> float:
        total = 0.0
        for stat, weight in self.weights.items():
            value = stats.get(stat)
            if value is not None:
                total += float(value) * weight
        return total


# Default full-PPR scoring. Standard offensive categories only for the
# walking skeleton; K/DST fidelity is a later concern (see DESIGN "Open items").
DEFAULT_PPR = ScoringConfig(
    weights={
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "pass_2pt": 2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "rush_2pt": 2.0,
        "rec": 1.0,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "rec_2pt": 2.0,
        "fum_lost": -2.0,
    }
)
