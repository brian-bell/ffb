# Data sources

How `ffb` gets its numbers: every external source, what it provides, how it's
accessed, how it's parsed, and where it plugs into the pipeline. This is the
implementation reality — for the product rationale see [`DESIGN.md`](../DESIGN.md).

## Principles that apply to every source

- **Public / free / read-only.** No paid feeds; no writes back to any service.
- **Offline by default.** Every raw pull is snapshotted to `snapshots/<key>.json`
  on first fetch and replayed on later runs, so rebuilds and tests never re-hit
  the network. `--refresh` forces a re-fetch and updates the snapshot; crosswalk
  and FFC refreshes validate the parsed payload first so an empty/bad response
  cannot replace their last known-good cache. Tests and CI read committed
  `tests/fixtures/` instead of `snapshots/` (gitignored).
- **Thin fetch + pure parse.** Each source module is a `fetch_*` (network, returns
  raw JSON) paired with a pure `parse_*` (raw → normalized rows, never raises on a
  bad row — it logs and skips). Only `fetch_*` touches the network; only
  `store.py` touches DuckDB.
- **Canonical identity via the crosswalk.** Every source's native player id is
  resolved to a canonical `player_key` (nflverse `mfl_id`) so consensus aligns the
  same player across sources. A miss is never dropped — it falls back to a
  `source:native_id` key with `matched=False` and is surfaced. The one exception
  is FFC ADP, whose id has no crosswalk column: it resolves by **name + position**
  instead (`names.py`), with the same never-dropped fallback.
- **Points are computed, never stored.** Sources contribute stat lines;
  `scoring.py` scores them at read time. A source's own point total is kept only
  as `src_pts_ppr` for validation.

## At a glance

| Source | Access | Auth | Provides | Status |
|---|---|---|---|---|
| **Sleeper projections** | REST JSON (`api.sleeper.com`) | none | Season projections (all offensive + K/DEF stat lines) | **Live** |
| **ESPN projections** | Unofficial REST JSON (`lm-api-reads.fantasy.espn.com`) | none | Season projections (offense; K/DST not decoded yet) | **Live** |
| **nflverse `ff_playerids`** | `nflreadpy` (parquet → polars) | none | Identity crosswalk across mfl/sleeper/espn/yahoo/gsis ids | **Live** |
| **Fantasy Football Calculator** | REST JSON (`fantasyfootballcalculator.com`) | none | ADP (draft value-vs-cost) for the cheat sheet | **Live** |
| Yahoo league fixture | Local JSON (`LeagueBundle` v1) | none | League scoring, roster slots, teams, current-week rosters | **Live (mock only)** |
| Yahoo Fantasy | `yfpy` (REST + OAuth2) | OAuth2 | Live league scoring, roster slots, teams, rosters | Planned (Task 2b) |
| nflverse stats/injuries/depth | `nflreadpy` | none | Usage (snaps/targets), injury designations, depth charts | Planned (in-season) |
| Sleeper trending / player status | REST JSON | none | Trending adds/drops, injury status | Planned (slice 11) |
| ESPN news / RSS | REST / RSS | none | Headlines for LLM digest (never numeric) | Planned (slice 13) |

---

## Live sources

### 1. Sleeper — season projections

`src/ffb/sources/sleeper.py`. The always-on primary projection source (a run with
no `--sources` uses Sleeper alone).

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
  - D/ST lines are sparse (only `pts_allow_0` among the points-allowed bands) and
    Sleeper labels defenses `DEF` (ESPN uses `DST`).

### 2. ESPN — season projections

`src/ffb/sources/espn.py`. Second projection source; added with `--sources` to
form a consensus. **Unofficial, undocumented endpoint** — no auth, may drift.

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
  - Position via `config.ESPN_POSITION_MAP` (`defaultPositionId` → `QB/RB/WR/TE/K/DST`).
  - Stats via `config.ESPN_STAT_MAP` — numeric stat ids decoded to the **same stat
    keys Sleeper uses** (e.g. `3→pass_yd`, `53→rec`, `72→fum_lost`), so one scoring
    function scores both sources identically. Unmapped ids are dropped.
  - `team=None` (ESPN gives a numeric `proTeamId`; canonical team comes from the
    crosswalk). `src_pts_ppr=None` — ESPN's `appliedTotal` is 0 in this view, so we
    score it ourselves.
  - **Rows with no scorable stats are skipped.** K and D/ST report stat ids that
    are *not* in `ESPN_STAT_MAP` (only offense is decoded), so their translated
    line is empty; emitting them would drag a crosswalk-matched kicker's consensus
    to half. Skipping is self-healing — once those ids are mapped, the rows flow
    through automatically.
- **Snapshot key** — `espn/projections_{season}`.
- **Gotchas**
  - Unofficial endpoint: if ESPN changes shape, the committed fixture keeps CI
    green; re-verify with a fresh `--refresh` pull.
  - K/DST stat ids are **not decoded yet** (tracked as a follow-up), so under
    `--sources` those positions are effectively Sleeper-only.
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
  players adopt the crosswalk's position — without this, `--pos K` misses matched
  kickers). Rows without an `mfl_id` are skipped.
- **How it's used** — `store.upsert_crosswalk`/`replace_crosswalk` load the spine;
  `store.resolve` / `resolve_batch` map a source's native id (`sleeper_id`,
  `espn_id`, or `yahoo_id`) to `player_key`. Fixture roster misses remain stored
  as `yahoo:<id>` fallbacks.
- **Snapshot key** — `nflverse/ff_playerids`.
- **Gotchas**
  - A `--refresh` **mirrors** the source (replace, not union) so ids removed or
    reassigned upstream don't leave stale mappings — but an empty/malformed pull
    is rejected so it can't wipe a usable spine.
  - **Team defenses are not in `ff_playerids`.** DEF/DST rows never match, so they
    fall back to `source:native_id` keys and don't form a cross-source consensus.

### 4. Fantasy Football Calculator — ADP

`src/ffb/sources/ffc.py`. Not a projection source — **average draft position**,
the market's cost-of-acquisition, joined onto the consensus so the cheat sheet can
show value (`ffb cheatsheet`). Free, no auth, informal (no SLA, undocumented).

- **Endpoint**
  ```
  GET https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={N}&year={season}
  ```
  One format/teams combo matching the league: `fmt = config.FFC_FORMAT` (`"ppr"`),
  `N = config.LEAGUE_NUM_TEAMS` (`12`). Header `User-Agent: ffb/0.1 (personal
  use)`, `Accept: application/json`, 30s timeout. **No auth.**
- **Response** — `{"status", "meta": {type, teams, rounds, total_drafts,
  start_date, end_date}, "players": [{player_id, name, position, team, adp,
  adp_formatted, times_drafted, high, low, stdev, bye}]}` (~200 players; positions
  `QB RB WR TE PK DEF`; DEF rows named like `"San Francisco Defense"`).
- **What we extract** (`parse_adp`, pure): requires `status == "Success"` (anything
  else, or a non-object payload, returns `[]` so the snapshot `is_valid` gate treats
  a bad refresh as empty). Per row: stringify `player_id`; normalize position via
  `config.FFC_POSITION_MAP` (`PK → K`, `DEF` kept); alias the team via
  `config.FFC_TEAM_ALIASES` (`SF → SFO`, `KC → KCC`, …) to nflverse/MFL style so the
  name matcher's team tiebreak compares like with like. Rows with a missing or
  non-string name/position are skipped (a bad value would otherwise crash name
  resolution). Never raises on a bad row — it logs and skips.
- **Identity — by name, not id.** FFC's `player_id` is FFC-internal (no column in
  `ff_playerids`), so `resolve_batch` can't apply. `names.build_name_index` /
  `match_by_name` resolve each row by `(normalized name, position)`:
  normalization lowercases and strips punctuation, diacritics, and generational
  suffixes (`Jr`/`III`); duplicate `(name, pos)` pairs (the crosswalk has ~551,
  mostly retired players on team `FA`) are disambiguated by dropping `FA`
  candidates, then a team tiebreak. **Ambiguity resolves to unmatched, never a
  guess** — a wrong merge silently corrupts the board. A miss falls back to
  `ffc:<player_id>` (`matched=False`) and is reported in the CLI footer.
- **Storage** — the `adp` table (keyed by `(player_key, season, source)`), carrying
  FFC's own name/pos/team plus the ADP fields. ADP is a **source value, so it is
  stored** (unlike computed points/VORP/tiers).
- **Ingest** (`ensure_adp_ingested`) — has no id-based staleness detector (its
  resolution is name-based, invisible to `has_stale_resolution`), so it re-parses +
  re-resolves from the cached snapshot on **every** run (delete-then-insert mirror).
  That keeps the mirror honest and buys the late-crosswalk self-heal for free. An
  invalid/empty pull is surfaced as a failure so the CLI drops ADP rather than
  serving a stale slice.
- **Snapshot key** — `ffc/adp_{fmt}_{teams}_{season}` (e.g. `ffc/adp_ppr_12_2024`).
- **Gotchas**
  - **Team defenses ride ADP-only.** DEF isn't in `ff_playerids`, so FFC `DEF`
    rows fall back to `ffc:` keys that can't join Sleeper's `sleeper:` DEF
    fallbacks — a defense appears on the board with ADP but no projection↔ADP join.
    Kickers are fine (`PK → K`, kickers are in the crosswalk).
  - Expect ~a dozen unmatched ADP rows on real data (mostly defenses, plus
    nickname/ambiguous misses like "Hollywood Brown" or two "Mike Williams");
    extending `normalize_name`/`FFC_TEAM_ALIASES` is the tuning loop, not a bug.
  - One format/teams combo per pull — a 10-team or half-PPR league shifts these
    (and the VORP baselines), but both are config reads.

---

## The access layer: snapshot cache

`src/ffb/snapshot.py` (`SnapshotCache`). Wraps every fetch so the pipeline is
offline-first.

- `get_json(key, fetch, *, refresh=False, is_valid=None)`:
  - On a cache **hit** (file exists, not `refresh`): return the parsed JSON,
    `fetch` is never called (no network).
  - On a **miss** (or `refresh`): call `fetch()`, and — if `is_valid` passes —
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

`ingest.ensure_crosswalk` loads the spine; `ensure_ingested` (Sleeper) and
`ensure_espn_ingested` load each projection source, resolving native ids to
`player_key`. `resolve_rows` gives matched players the canonical crosswalk
identity (so one source can't clobber another's name/team) and keeps misses under
a fallback key. `consensus.py` groups by `player_key`, scores each source's stat
line with `config.LEAGUE_SCORING`, and averages the points (carrying `n`, the
source count).

`league.py` validates a closed, provider-neutral `LeagueBundle` before
`Store.replace_league_state` atomically mirrors settings/teams and replaces only
the current roster week. `league_context.py` selects complete fixture scoring and
roster components independently, falling back to placeholders safely.

`ensure_adp_ingested` runs the same fetch → snapshot → parse path but resolves by
name (`names.py`) into the `adp` table. `ffb cheatsheet` then left-joins that ADP
onto the consensus in `board.py`, adds VORP (`vorp.py`) and positional tiers
(`tiers.py`), and renders the board / `board.json` — all computed at read time, so
a scoring or roster-shape change re-derives everything with no re-ingest.

---

## Planned sources (not yet wired)

Documented for context; see `DESIGN.md` and the slice roadmap. These live sources
are not ingested today.

- **Yahoo Fantasy API** via `yfpy` (Task 2b) — the live adapter for the league's
  own state. Fixture-backed current-week league state is already supported through
  `ffb league sync --fixture`; no credentials or live requests exist in this slice.
  The live adapter will add exact scoring settings, roster slots, teams, rosters, free agents, matchups,
  transactions, draft results, and actual weekly points. OAuth2 (token persisted
  between runs); **read-only** (the pipeline recommends, never sets lineups). The
  crosswalk already carries `yahoo_id` for the join. Its scoring settings will
  replace the placeholder `config.LEAGUE_SCORING`.
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
committed fixtures keep CI green while the parser is re-verified against a fresh
`--refresh` pull.
