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

# Default for optional positional CLI season arguments.
DEFAULT_SEASON = 2026

# Sleeper returns multiple projection companies per player; pin one for
# determinism in the walking skeleton. Consensus across companies/sources
# arrives in slice 3.
SLEEPER_COMPANY = "rotowire"

# Standard lineup positions. Projection parsers drop any row outside this set
# at parse time. Future IDP opt-in = extend this set (ESPN_POSITION_MAP already
# decodes DT/DE/LB/CB/S) and add IDP weights to LEAGUE_SCORING.
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

SLEEPER_POSITIONS = FANTASY_POSITIONS  # fetch-side position[] filter

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
    # Kicking. ESPN also emits attempts, misses, aggregate FGM, and overlapping
    # 50-59/60+ splits; keep only this non-overlapping made-FG partition + XP.
    # ESPN's under-40 bucket maps to one of Yahoo's equal-value three-point
    # bands so it is counted once without inventing a distance distribution.
    74: "fgm_50p",
    77: "fgm_40_49",
    80: "fgm_30_39",
    86: "xpm",
    # Team defense / special teams. Multiple source buckets intentionally map
    # to the same league key; the ESPN parser adds them rather than overwriting.
    89: "pts_allow_0",
    90: "pts_allow_1_6",
    91: "pts_allow_7_13",
    92: "pts_allow_14_20",
    93: "def_fum_td",  # blocked-kick TD: same six-point defensive-TD bucket
    95: "int",
    96: "fum_rec",
    97: "blk_kick",
    98: "safe",
    99: "sack",
    101: "def_ret_td",
    102: "def_ret_td",
    103: "def_fum_td",
    104: "pass_int_td",
    121: "pts_allow_14_20",  # ESPN 18-21; chosen approximation for Yahoo 14-20
    122: "pts_allow_21_27",
    123: "pts_allow_28_34",
    124: "pts_allow_35p",
    125: "pts_allow_35p",
}

# ESPN defaultPositionId -> our position label. IDP ids (9-13) are decoded for
# diagnostics and a future IDP opt-in but excluded by FANTASY_POSITIONS today.
# The labels are ESPN's own classification (cross-checked against the nflverse
# crosswalk 2026-07-23: each id maps dominantly onto that position, with tails
# like 10<->LB), not identity ground truth.
ESPN_POSITION_MAP = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    9: "DT",
    10: "DE",
    11: "LB",
    12: "CB",
    13: "S",
    16: "DEF",
}

# ESPN proTeamId -> the nflverse/MFL-style team codes used by canonical player
# identity. ESPN's retired OAK label for id 13 is normalized to current LVR.
ESPN_PRO_TEAM_MAP = {
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GBP",
    10: "TEN",
    11: "IND",
    12: "KCC",
    13: "LVR",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NEP",
    18: "NOS",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SFO",
    26: "SEA",
    27: "TBB",
    28: "WAS",
    29: "CAR",
    30: "JAC",
    33: "BAL",
    34: "HOU",
}

# --- Yahoo league adapter (task 2b) -----------------------------------------
# OAuth pieces that must match the registered Yahoo developer app exactly. The
# redirect URI is part of the token-grant contract, so it lives in config (with
# an FFB_YAHOO_REDIRECT_URI env override) rather than hardcoded in the flow.
# Client id/secret are env-only (FFB_YAHOO_CLIENT_ID / FFB_YAHOO_CLIENT_SECRET)
# and never appear in code, logs, or snapshots.
YAHOO_REDIRECT_URI = "https://ffb.bbell.dev/auth/yahoo/callback"
YAHOO_TOKEN_PATH = DATA_DIR / "yahoo_token.json"  # gitignored with the rest of data/

# Yahoo stat_id -> our Sleeper-style stat keys, feeding settings.scoring_rules;
# ids absent here surface in unmapped_scoring_rules instead of being dropped.
# Static and best-effort from documented NFL stat ids: modeled from
# documentation, not observed traffic, pending the ffb-1ct.2 correction pass.
# Multi-key entries fan one Yahoo category out across the split keys our stat
# lines use (each fanned rule gets a "<id>.<key>" provider_stat_id).
YAHOO_STAT_MAP: dict[int, tuple[str, ...]] = {
    4: ("pass_yd",),
    5: ("pass_td",),
    6: ("pass_int",),
    9: ("rush_yd",),
    10: ("rush_td",),
    11: ("rec",),
    12: ("rec_yd",),
    13: ("rec_td",),
    # Yahoo scores one "2-Point Conversions" category across pass/rush/rec.
    16: ("pass_2pt", "rush_2pt", "rec_2pt"),
    18: ("fum_lost",),
    57: ("fum_rec_td",),
    # Kicking: made-FG distance bands + PAT.
    19: ("fgm_0_19",),
    20: ("fgm_20_29",),
    21: ("fgm_30_39",),
    22: ("fgm_40_49",),
    23: ("fgm_50p",),
    29: ("xpm",),
    # Team defense / special teams.
    32: ("sack",),
    33: ("int",),
    34: ("fum_rec",),
    # Yahoo's single defensive "Touchdown" spans INT and fumble return TDs.
    35: ("pass_int_td", "def_fum_td"),
    36: ("safe",),
    37: ("blk_kick",),
    49: ("def_ret_td",),  # combined kickoff + punt return TD on D/ST rows
    50: ("pts_allow_0",),
    51: ("pts_allow_1_6",),
    52: ("pts_allow_7_13",),
    53: ("pts_allow_14_20",),
    54: ("pts_allow_21_27",),
    55: ("pts_allow_28_34",),
    56: ("pts_allow_35p",),
}

NFL_TEAM_CODES = frozenset(ESPN_PRO_TEAM_MAP.values())
# Source/API abbreviations normalized to the MFL-style codes used by the
# crosswalk and synthetic defense keys.
TEAM_ALIASES = {
    "SF": "SFO",
    "KC": "KCC",
    "GB": "GBP",
    "NE": "NEP",
    "NO": "NOS",
    "TB": "TBB",
    "LV": "LVR",
    "JAX": "JAC",
    "LA": "LAR",  # nflverse schedules label the Rams "LA"
    "OAK": "LVR",  # retired relocation code, still seen in stale crosswalk rows
}


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


# Default full-PPR scoring for generic library callers. League-specific kicking
# and defense weights live in LEAGUE_SCORING below.
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
# The league's confirmed Yahoo scoring, applied to consensus projections so
# rankings reflect THIS league, not generic PPR. Hand-entered from the league
# settings on 2026-07-27: half-PPR, 1 point per 20 passing yards, no turnover
# penalties, and no kicking rules. Swapping weights re-scores everything with no
# re-ingest (points are computed at read time — see AGENTS.md). Sleeper and ESPN
# are normalized onto these keys; one flat map is safe because offensive and
# defensive interception keys are distinct.
LEAGUE_SCORING = ScoringConfig(
    weights={
        # Offense
        "pass_yd": 0.05,  # 1 pt / 20 yd
        "pass_td": 4.0,
        "pass_2pt": 2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "rush_2pt": 2.0,
        "rec": 0.5,  # half PPR
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "rec_2pt": 2.0,
        "fum_rec_td": 6.0,
        # Team defense / special teams.
        "sack": 1.0,
        "int": 2.0,  # defensive interception (distinct from a QB's pass_int)
        "fum_rec": 2.0,
        "safe": 2.0,
        "blk_kick": 2.0,
        # Defensive + return TDs, all 6 pts. Kick and punt returns are normalized
        # to def_ret_td only on D/ST rows so individual returners do not score.
        "def_fum_td": 6.0,
        "pass_int_td": 6.0,
        "def_ret_td": 6.0,
        "def_2pt": 2.0,
        # The league's configured points-allowed ladder ends at 21–27 (zero).
        # Higher bands are unconfigured and therefore score zero by omission.
        "pts_allow_0": 10.0,
        "pts_allow_1_6": 7.0,
        "pts_allow_7_13": 4.0,
        "pts_allow_14_20": 1.0,
        "pts_allow_21_27": 0.0,
    }
    # Sleeper also emits non-standard categories we deliberately don't score
    # (IDP tackles, first downs, per-distance reception bands, PPR bonuses); they
    # aren't in this standard-ish league and are why src_pts_ppr can diverge.
)

# Confirmed Yahoo lineup, using the API-style slash labels for the W-T and W-R-T
# flexes shown in the UI. Eight starters plus eight bench slots feed VORP
# replacement baselines and the lineup optimizer.
LEAGUE_ROSTER_SLOTS = {
    "QB": 1,
    "WR": 1,
    "RB": 1,
    "TE": 1,
    "W/T": 1,
    "W/R/T": 2,
    "DEF": 1,
    "BN": 8,
}

# Confirmed number of teams. Feeds VORP replacement baselines (§3e: slots to
# fill = teams × starting slots) and the FFC ADP pull.
LEAGUE_NUM_TEAMS = 10

# --- FFC ADP source (slice 5, spike-verified 2026-07-21) --------------------
# Fantasy Football Calculator's free ADP API. Pull the confirmed 10-team,
# half-PPR format. `PK` is FFC's kicker label (we normalize to `K`).
FFC_FORMAT = "half-ppr"
FFC_POSITION_MAP = {"PK": "K", "DEF": "DEF"}

# --- Cheat sheet: VORP + tiers (slice 5) ------------------------------------
# Draftable pool depth per position — how deep tiers/board consider a position
# before overflow. Plain numbers tuned by eye (teams × dedicated starters + flex
# + bench share), not derived cleverly; revisit only if the board looks wrong on
# real data.
POSITION_POOL = {"QB": 16, "RB": 48, "WR": 48, "TE": 16, "K": 12, "DEF": 12}

# Number of tiers per position (largest-gap splits within the pool). Config knobs;
# tests assert the tiering mechanics, not these specific counts.
TIER_COUNT = {"QB": 6, "RB": 8, "WR": 8, "TE": 6, "K": 3, "DEF": 3}
