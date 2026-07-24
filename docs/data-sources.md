# Data sources

How `ffb` gets its numbers: every external source, what it provides, how it's
accessed, how it's parsed, and where it plugs into the pipeline. This is the
implementation reality — for the product rationale see [`DESIGN.md`](../DESIGN.md).

## Principles that apply to every source

- **Public / free / read-only.** No paid feeds; no writes back to any service.
- **Explicit synchronization.** `ffb season sync` is the only
  projection/ADP/schedule ingest path. Every raw pull is snapshotted to `snapshots/<key>.json`.
  Missing-only fetches absent snapshots, refresh fetches all selected datasets,
  and offline prohibits network access. Every source validates parsed payloads
  so an empty/bad response cannot replace its last known-good cache. Tests and CI read committed
  `tests/fixtures/` instead of `snapshots/` (gitignored).
- **Thin fetch + pure parse.** Each source module is a `fetch_*` (network, returns
  raw JSON) paired with a pure `parse_*` (raw → normalized rows, never raises on a
  bad row — it logs and skips). Only `fetch_*` touches the network; only
  `store.py` touches DuckDB.
- **Canonical identity via the crosswalk.** Every source's native player id is
  resolved to a canonical `player_key` (nflverse `mfl_id`) so consensus aligns the
  same player across sources. A miss is retained under a `source:native_id` key
  with `matched=False` for diagnostics and later self-healing, but is excluded
  from rankings and the draft board. The one exception is FFC ADP, whose id has
  no crosswalk column: it resolves by **name + position** instead (`names.py`),
  with the same stored fallback.
- **Points are computed, never stored.** Sources contribute stat lines;
  `scoring.py` scores them at read time. A source's own point total is kept only
  as `src_pts_ppr` for validation.

## At a glance

| Source | Access | Auth | Provides | Status |
|---|---|---|---|---|
| **Sleeper projections** | REST JSON (`api.sleeper.com`) | none | Season projections (all offensive + K/DEF stat lines) | **Live** |
| **ESPN projections** | Unofficial REST JSON (`lm-api-reads.fantasy.espn.com`) | none | Season projections (offense + K/DEF stat lines) | **Live** |
| **nflverse `ff_playerids`** | `nflreadpy` (parquet → polars) | none | Identity crosswalk across mfl/sleeper/espn/yahoo/gsis ids | **Live** |
| **Fantasy Football Calculator** | REST JSON (`fantasyfootballcalculator.com`) | none | ADP (draft value-vs-cost) for the cheat sheet | **Live** |
| **nflverse schedules** | `nflreadpy` (parquet → polars) | none | Season schedule → one bye week per team | **Live** |
| Yahoo league fixture | Local JSON (`LeagueBundle` v1) | none | League scoring, roster slots, teams, current-week rosters | **Implemented (fixture only)** |
| Yahoo Fantasy | `yfpy` (REST + OAuth2) | OAuth2 | Live league scoring, roster slots, teams, rosters | Planned (Task 2b) |
| nflverse stats/injuries/depth | `nflreadpy` | none | Usage (snaps/targets), injury designations, depth charts | Planned (in-season) |
| Sleeper trending / player status | REST JSON | none | Trending adds/drops, injury status | Planned (slice 11) |
| ESPN news / RSS | REST / RSS | none | Headlines for LLM digest (never numeric) | Planned (slice 13) |

---

## Live sources

### 1. Sleeper — season projections

`src/ffb/sources/sleeper.py`. The primary projection source.

- **Endpoint**
  ```
  GET https://api.sleeper.com/projections/nfl/{season}
      ?season_type=regular
      &order_by=pts_ppr
      &position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF
  ```
  Positions come from `config.SLEEPER_POSITIONS`. Header `User-Agent: ffb/0.1
  (personal use)`, 30s timeout. **No auth.**
- **Response** — a JSON list of rows, one per player-per-company. Each row has:
  - `player` — `first_name`, `last_name`, `position`, `team`
  - `player_id` — Sleeper's native id (→ crosswalk `sleeper_id`)
  - `company` — the projection provider (e.g. `rotowire`, `sportradar`)
  - `stats` — the raw stat line: fantasy stats **plus** Sleeper's own point totals
    (`pts_ppr`, `pts_std`, `pts_half_ppr`) and ADP fields (`adp_ppr`, …)
- **What we extract** (`parse_projections`): keep only rows where `company ==
  config.SLEEPER_COMPANY` (`"rotowire"`, pinned for determinism); drop rows with no
  player/position/id; dedupe by `player_id`. Each kept row becomes
  `{native_id, full_name, position, team, season, source="sleeper",
  scope="season", stats, src_pts_ppr=stats.pts_ppr}`. The **entire raw `stats`
  dict is stored** — scoring later picks out the keys it knows.
- **Stat keys actually scored** (via `config.LEAGUE_SCORING`): offense
  (`pass_yd/td/int/2pt`, `rush_yd/td/2pt`, `rec/rec_yd/rec_td/rec_2pt`,
  `fum_lost`), kicking (`fgm_40_49`, `fgm_50p`, `xpm`), and D/ST (`sack`, `int`,
  `fum_rec`, `safe`, `blk_kick`, `def_fum_td`, `def_kr_td`, `pass_int_td`,
  `pr_td`, `pts_allow_0`). Everything else Sleeper emits (`adp_*`, `bonus_rec_*`,
  `idp_*`, per-distance receptions, first downs, attempts) is intentionally
  unscored and ignored at read time.
- **Snapshot key** — `sleeper/projections_nfl_{season}_regular`.
- **Gotchas**
  - One company is pinned; multi-company averaging is not done.
  - The rotowire kicker line only projects **40+ yard** FGs (`fgm_40_49`,
    `fgm_50p`) — there is no total-FGM or sub-40 band, so kicker points reflect
    long FGs + XP (this matches Sleeper's own `pts_std`; validated to ~2 pt median
    diff in `tests/test_scoring_validation.py`).
  - D/ST lines are sparse (only `pts_allow_0` among the points-allowed bands).

### 2. ESPN — season projections

`src/ffb/sources/espn.py`. Second projection source, combined with persisted
Sleeper data to form a consensus. **Unofficial, undocumented endpoint** — no
auth, may drift.

- **Endpoint**
  ```
  GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/players
      ?view=kona_player_info&scoringPeriodId=0
  Header x-fantasy-filter: {"players":{"limit":5000,
                            "sortPercOwned":{"sortAsc":false,"sortPriority":1}}}
  Header Accept: application/json,  User-Agent: ffb/0.1 (personal use)
  ```
  60s timeout. The `limit` (`DEFAULT_LIMIT = 5000`) is deliberately above the full
  player universe (~2,900) so the low-owned tail is never silently dropped.
- **Response** — a **bare top-level JSON list** of player objects (not wrapped in
  `{"players": …}`). Each has `id`, `fullName`, `defaultPositionId`, and a
  `stats[]` array. The **season projection** is the `stats[]` entry with
  `statSourceId == 1 && scoringPeriodId == 0`; its `stats` is a
  `{numeric statId: value}` map.
- **What we extract** (`parse_projections`):
  - Position via `config.ESPN_POSITION_MAP` (`defaultPositionId` →
    `QB/RB/WR/TE/K/DEF`; ESPN's D/ST label is normalized to `DEF`).
  - Stats via `config.ESPN_STAT_MAP` — numeric stat ids decoded to the **same stat
    keys Sleeper uses** for offense, kicking, and defense. Multiple ESPN buckets
    may add into one league category; notably ESPN's 18–21 points-allowed band is
    deliberately approximated as Yahoo's 14–20 band. Unmapped ids are dropped.
  - Numeric `proTeamId` maps to the canonical MFL-style team code used by the
    crosswalk and defense identity. `src_pts_ppr=None` — ESPN's `appliedTotal` is
    0 in this view, so we score it ourselves.
  - **Rows with no decoded scorable stats are skipped** rather than emitted at
    zero, which avoids incorrectly diluting a cross-source consensus.
- **Snapshot key** — `espn/projections_{season}`.
- **Gotchas**
  - Unofficial endpoint: if ESPN changes shape, the committed fixture keeps CI
    green; re-verify with `ffb season sync [SEASON] --refresh --source espn`.
  - K/DEF now form a Sleeper + ESPN consensus. Kickers join through the player
    crosswalk; defenses join through `def:<canonical team>`.
  - `appliedTotal == 0` here — never trust it as a point total.

### 3. nflverse `ff_playerids` — the identity crosswalk

`src/ffb/sources/crosswalk.py`. Not a projection source — the **join spine** that
lets consensus align the same player across Sleeper and ESPN, plus fixture-backed
Yahoo roster identities.

- **Access** — `nflreadpy.load_ff_playerids()` returns a wide polars DataFrame
  (parquet under the hood). **Use `nflreadpy`, not `nfl_data_py`** — the latter
  pins `pandas==1.5.3`, which won't build on Python 3.12. The polars frame never
  leaves the module: we `select(_FETCH_COLS).to_dicts()` and hand back plain
  `list[dict]`. **No auth.**
- **Columns kept** (`_FETCH_COLS`): `mfl_id`, `sleeper_id`, `espn_id`, `yahoo_id`,
  `gsis_id`, `name`, `position`, `team`.
- **What we extract** (`parse_crosswalk`): `player_key = mfl_id` (the canonical
  id). Every id is **stringified and null-guarded** (nflverse ids are polars
  `Int64`, so joins must be string-to-string). Position is normalized **`PK` → `K`**
  (nflverse labels place-kickers `PK`; sources and the league use `K`, and matched
  players adopt the crosswalk's position — without this, `--position K` misses matched
  kickers). Rows without an `mfl_id` are skipped.
- **How it's used** — `store.upsert_crosswalk`/`replace_crosswalk` load the spine;
  `store.resolve` / `resolve_batch` map a source's native id (`sleeper_id`,
  `espn_id`, or `yahoo_id`) to `player_key`. Fixture roster misses remain stored
  as `yahoo:<id>` fallbacks.
- **Snapshot key** — `nflverse/ff_playerids`.
- **Gotchas**
  - A refresh sync **mirrors** the source (replace, not union) so ids removed or
    reassigned upstream don't leave stale mappings — but an empty/malformed pull
    is rejected so it can't wipe a usable spine.
  - **Team defenses are not in `ff_playerids`.** Valid DEF/DST rows bypass it and
    use the synthetic `def:<canonical MFL team code>` identity; unknown team codes
    remain source fallbacks rather than risking a wrong merge.

### 4. Fantasy Football Calculator — ADP

`src/ffb/sources/ffc.py`. Not a projection source — **average draft position**,
the market's cost-of-acquisition, joined onto the consensus so the board can
show value (`ffb board show`). Free, no auth, informal (no SLA, undocumented).

- **Endpoint**
  ```
  GET https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={N}&year={season}
  ```
  One format/teams combo matching the league: `fmt = config.FFC_FORMAT` (`"ppr"`),
  while `N` comes from the stored league context and falls back to
  `config.LEAGUE_NUM_TEAMS` (`12`). Header `User-Agent: ffb/0.1 (personal use)`,
  `Accept: application/json`, 30s timeout. **No auth.**
- **Response** — `{"status", "meta": {type, teams, rounds, total_drafts,
  start_date, end_date}, "players": [{player_id, name, position, team, adp,
  adp_formatted, times_drafted, high, low, stdev, bye}]}` (~200 players; positions
  `QB RB WR TE PK DEF`; DEF rows named like `"San Francisco Defense"`).
- **What we extract** (`parse_adp`, pure): requires `status == "Success"` (anything
  else, or a non-object payload, returns `[]` so the snapshot `is_valid` gate treats
  a bad refresh as empty). Per row: stringify `player_id`; normalize position via
  `config.FFC_POSITION_MAP` (`PK → K`, `DEF` kept); alias the team via
  `config.TEAM_ALIASES` (`SF → SFO`, `KC → KCC`, …) to nflverse/MFL style so the
  name matcher's team tiebreak compares like with like. Rows with a missing or
  non-string name/position are skipped (a bad value would otherwise crash name
  resolution). Never raises on a bad row — it logs and skips.
- **Identity — by name, not id.** Team defenses first use `def:<canonical team>`.
  FFC's player ids are internal (no column in `ff_playerids`), so other rows use
  `names.build_name_index` /
  `match_by_name` resolve each row by `(normalized name, position)`:
  normalization lowercases and strips punctuation, diacritics, and generational
  suffixes (`Jr`/`III`); duplicate `(name, pos)` pairs (the crosswalk has ~551,
  mostly retired players on team `FA`) are disambiguated by dropping `FA`
  candidates, then a team tiebreak. **Ambiguity resolves to unmatched, never a
  guess** — a wrong merge silently corrupts the board. A miss falls back to
  `ffc:<player_id>` (`matched=False`), appears in `season unmatched`, and is
  excluded from the draft board.
- **Storage** — the `adp` table (keyed by `(player_key, season, source)`), carrying
  FFC's own name/pos/team plus the ADP fields. ADP is a **source value, so it is
  stored** (unlike computed points/VORP/tiers).
- **Ingest** (`ensure_adp_ingested`) — has no id-based staleness detector (its
  resolution is name-based, invisible to `has_stale_resolution`), so it re-parses +
  re-resolves whenever explicitly synchronized (atomic replace).
  That keeps the mirror honest and buys the late-crosswalk self-heal for free. An
  invalid/empty pull is surfaced as a failure while retaining known-good ADP.
- **Snapshot key** — `ffc/adp_{fmt}_{teams}_{season}` (e.g. `ffc/adp_ppr_12_2024`).
- **Gotchas**
  - Team defenses join projection consensus through `def:<canonical team>` even
    though they are absent from `ff_playerids`. Kickers join through the crosswalk.
  - Expect an unmatched ADP tail from nickname/ambiguous misses like "Hollywood
    Brown" or two "Mike Williams"; extending `normalize_name`/`TEAM_ALIASES` is
    the tuning loop, not a bug.
  - One format/teams combo per pull. Changing the stored league team count marks
    the existing FFC state stale until `ffb season sync SEASON --source ffc`
    loads the corresponding snapshot. The format is still the configured `ppr`;
    half-PPR would require a configuration change.

### 5. nflverse schedules — team bye weeks

`src/ffb/sources/schedule.py`. Not a projection source — the **complete season
schedule**, from which one bye week per team is derived so the board never
depends on FFC's non-exhaustive ADP list for byes.

- **Access** — `nflreadpy.load_schedules(seasons=[season])` returns a wide polars
  DataFrame (272 regular-season games). Like the crosswalk, the frame never
  leaves the module: `select(_FETCH_COLS).to_dicts()` hands back plain
  `list[dict]`. Columns kept: `season`, `game_type`, `week`, `home_team`,
  `away_team`. **No auth.**
- **What we extract** (`parse_byes`, pure): only `game_type == "REG"` games
  count. Each team's bye is the **single missing week** in `1..max_week` across
  its games. A team missing zero or multiple weeks (incomplete schedule) is
  logged and skipped — **never guessed** — as is any unknown team code, so an
  incomplete pull yields fewer byes, not wrong ones. Returns `[]` for an
  empty/wrong-shaped payload (the snapshot `is_valid` gate).
- **Identity — canonical team codes.** Schedule codes are nflverse style
  (`KC`/`SF`/`LA`); every code routes through `identity.canonical_team`
  (`config.TEAM_ALIASES` carries `LA → LAR` for this source, plus retired
  `OAK → LVR` for stale crosswalk teams on the join side).
- **Storage** — the `team_byes` table, keyed `(season, source, team)`. Byes are
  a **source value, so they are stored** (like ADP, unlike computed points).
- **Ingest** (`ensure_schedule_ingested`) — re-parses + re-resolves from the
  cached snapshot on **every** run (atomic delete-then-insert mirror): parsing
  ~272 games is trivial, and a `TEAM_ALIASES` tuning change reaches the DB
  without `--refresh`. An empty parse is surfaced as a failure while retaining
  known-good byes.
- **How it's used** — `board.py` joins byes onto every row by canonical team
  (players, kickers, and `def:<team>` D/ST alike), independent of ADP. The
  schedule bye wins; FFC's per-player `bye` field is only a fallback when the
  schedule lacks the team.
- **Snapshot key** — `nflverse/schedule_{season}`.
- **Gotchas**
  - The schedule for an upcoming season is published in May; syncing before
    that surfaces a failed `schedule` source and the board falls back to FFC
    byes.
  - Cancelled/relocated games that leave a team with an ambiguous week set drop
    that team's bye rather than guessing.

---

## The access layer: snapshot cache

`src/ffb/snapshot.py` (`SnapshotCache`). Wraps every fetch with an explicit
policy and exposes key, modification time, and SHA-256 metadata.

- `get_json(key, fetch, *, policy=..., is_valid=None)`:
  - On a cache **hit** (unless refreshing): return the parsed JSON,
    `fetch` is never called (no network).
  - In offline mode, a cache miss raises an actionable error without calling
    `fetch`.
  - On an online **miss** (or refresh): call `fetch()`, and — if `is_valid` passes —
    write it to `snapshots/{key}.json` and return it. `is_valid` gates the write so
    a transient empty/garbage pull can't overwrite a known-good snapshot.
- Locations honor env overrides: `FFB_SNAPSHOT_DIR` (default `snapshots/`) and
  `FFB_DB_PATH` (default `data/ffb.duckdb`) via `paths.py`. Both `snapshots/` and
  `data/` are gitignored; the DB is a disposable cache rebuilt from snapshots.
- **Tests/CI** point at committed `tests/fixtures/*.json` (trimmed, hand-scrubbed
  real responses), so the suite is deterministic and network-free.

## From source to ranking (the flow)

```
fetch_*  ──►  SnapshotCache  ──►  parse_*  ──►  resolve_rows  ──►  store
(network)     (snapshots/)       (pure)        (crosswalk)        (DuckDB)
                                                                     │
                                            scoring.py (read-time)   ▼
                                          consensus.py  ◄──  projection_rows
```

`season_data.SeasonDataService` orchestrates explicit sync, status, and unmatched
diagnostics. `ingest.ensure_crosswalk` loads the spine; `ensure_ingested` (Sleeper) and
`ensure_espn_ingested` load each projection source, resolving native ids to
`player_key`. `resolve_rows` gives matched players the canonical crosswalk
identity (so one source can't clobber another's name/team) and keeps misses under
a fallback key. `consensus.py` excludes those unmatched fallbacks, groups matched
rows by `player_key`, scores each source's stat line with
`config.LEAGUE_SCORING`, and averages the points (carrying `n`, the source count).

`league.py` validates a closed, provider-neutral `LeagueBundle` before
`Store.replace_league_state` atomically mirrors settings/teams and replaces only
the current roster week. `league_context.py` selects complete fixture scoring and
roster components independently, falling back to placeholders safely.

`ensure_adp_ingested` runs the same fetch → snapshot → parse path but resolves by
name (`names.py`) into the `adp` table; `ensure_schedule_ingested` mirrors
schedule-derived team byes into `team_byes` the same way. `ffb board show/export`
then left-joins that ADP onto the consensus in `board.py`, joins byes by
canonical team, adds VORP (`vorp.py`) and positional tiers (`tiers.py`), and
renders the board / `board.json` — all computed at read time, so a scoring or
roster-shape change re-derives everything with no re-ingest.

---

## Planned sources (not yet wired)

Documented for context; see `DESIGN.md` and the slice roadmap. These live sources
are not ingested today.

- **Yahoo Fantasy API** via `yfpy` (Task 2b) — the live adapter for the league's
  own state. Fixture-backed current-week league state is already supported through
  `ffb league sync [SEASON] --fixture`; no credentials or live requests exist in this slice.
  The live adapter will add exact scoring settings, roster slots, teams, rosters, free agents, matchups,
  transactions, draft results, and actual weekly points. OAuth2 (token persisted
  between runs); **read-only** (the pipeline recommends, never sets lineups). The
  crosswalk already carries `yahoo_id` for the join. Live scoring, roster shape,
  and team count will use the same stored league-context path the fixture already
  exercises, with `config` values remaining component-level fallbacks.
- **nflverse stats / injuries / depth charts** via `nflreadpy` (in-season) —
  usage (snaps, targets), injury designations, practice participation, depth
  charts. Same access pattern as the crosswalk.
- **Sleeper player status + trending adds/drops** (slice 11) — waiver signal.
- **ESPN news / RSS headlines** (slice 13) — LLM-digested into per-player flags
  and prose; **never** adjusts numbers directly.

## Source hygiene

Public/free endpoints, personal use, polite request rates, everything snapshotted
so rebuilds don't re-fetch. A shared `User-Agent: ffb/0.1 (personal use)`
identifies the client. No source is written to. If a live endpoint drifts, the
committed fixtures keep CI green while the parser is re-verified with an
explicit refresh sync.
