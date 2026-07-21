"""Static configuration: paths, season, scoring constants.

These are deliberately plain module-level values for the walking skeleton.
``DEFAULT_PPR`` is the library's generic scoring default; ``LEAGUE_SCORING``
(slice 4) is *this league's* settings, applied by the CLI. It is a hand-entered
placeholder until slice 2 loads the real Yahoo settings from the store — at
which point the CLI's single call site swaps to a store read with
``LEAGUE_SCORING`` as the fallback. Keep scoring here (not hard-coded at call
sites) so that swap stays one edit.
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


# --- League scoring (slice 4) -----------------------------------------------
# The league's ACTUAL scoring, applied to consensus projections so rankings
# reflect THIS league, not generic PPR. These are typical Yahoo full-PPR
# defaults, hand-entered as a PLACEHOLDER until slice 2 pulls the real settings
# from the Yahoo league into the store. Swapping them re-scores everything with
# no re-ingest (points are computed at read time — see AGENTS.md). Known
# assumptions to confirm against the real league: full PPR (1.0), 4-pt passing
# TDs, -1 interceptions. Kicking and D/ST categories are included (keys map to
# Sleeper's stat line); ESPN's K/DST stat ids aren't decoded yet, so under
# --sources those positions are Sleeper-only until that decode lands (see espn.py
# and AGENTS.md). Stat keys don't collide across positions, so one flat map
# scores every position.
LEAGUE_SCORING = ScoringConfig(
    weights={
        # Offense
        "pass_yd": 0.04,  # 1 pt / 25 yd
        "pass_td": 4.0,
        "pass_int": -1.0,  # Yahoo default (generic DEFAULT_PPR uses -2)
        "pass_2pt": 2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "rush_2pt": 2.0,
        "rec": 1.0,  # full PPR
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "rec_2pt": 2.0,
        "fum_lost": -2.0,
        # Kicking — FG by distance + extra points. Sleeper's rotowire projection
        # only bands 40-49 and 50+; shorter bands are listed for portability and
        # are harmless no-ops when a source omits them.
        "fgm_0_19": 3.0,
        "fgm_20_29": 3.0,
        "fgm_30_39": 3.0,
        "fgm_40_49": 4.0,
        "fgm_50p": 5.0,
        "xpm": 1.0,
        # Team defense / special teams.
        "sack": 1.0,
        "int": 2.0,  # defensive interception (distinct from a QB's pass_int)
        "fum_rec": 2.0,
        "safe": 2.0,
        "blk_kick": 2.0,
        # Defensive + return TDs, all 6 pts. Keys verified against Sleeper: team
        # defensive returns sit on the D/ST row (def_fum_td, pass_int_td); kick and
        # punt returns sit on the returner's own offensive row (def_kr_td, pr_td).
        "def_fum_td": 6.0,
        "pass_int_td": 6.0,
        "def_kr_td": 6.0,
        "pr_td": 6.0,
        # Points-allowed ladder. Only pts_allow_0 shows in the current Sleeper
        # projection; the rest are standard Yahoo bands, scored if a source emits
        # them (harmless no-ops otherwise), same as the short-FG bands above.
        "pts_allow_0": 10.0,
        "pts_allow_1_6": 7.0,
        "pts_allow_7_13": 4.0,
        "pts_allow_14_20": 1.0,
        "pts_allow_21_27": 0.0,
        "pts_allow_28_34": -1.0,
        "pts_allow_35p": -4.0,
    }
    # Sleeper also emits non-standard categories we deliberately don't score
    # (IDP tackles, first downs, per-distance reception bands, PPR bonuses); they
    # aren't in this standard-ish league and are why src_pts_ppr can diverge.
)

# Typical 12-team starting lineup + bench, using Yahoo's position abbreviations
# ("W/R/T" flex, "DEF" defense) so slice 2's store-loaded slots drop in without
# renaming. Feeds VORP replacement baselines and the lineup optimizer (slices 5,
# 9). Placeholder until the real roster settings arrive.
LEAGUE_ROSTER_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "W/R/T": 1,
    "K": 1,
    "DEF": 1,
    "BN": 6,
}
