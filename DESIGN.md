# Fantasy Football Pipeline — Design

Decisions reached via design interview, 2026-07-20. This is the shared
understanding the implementation plan builds on.

## Purpose & scope

Personal tool for Brian's single Yahoo league: a **snake redraft, standard-ish**
league (~10–12 teams, 1 QB, K + DST, PPR-family — exact settings pulled from
Yahoo once authed; no superflex, no IDP). Free/public data sources only, no
multi-tenancy, no auth beyond what the tools themselves need.

Two deliverables:

1. **Draft support** (deadline: draft, late August) — tiered cheat sheet plus a
   live draft tracker web app.
2. **Weekly management** (deadline: Week 1, ~Sept 10) — sit/start, waivers,
   rest-of-season strategy, trade-offer evaluation, with an accuracy feedback
   loop.

## Data sources

| Data | Source | Notes |
|---|---|---|
| League state (roster, free agents, matchups, scoring settings, transactions) | Yahoo Fantasy API via `yfpy` | OAuth2; token refresh must persist between scheduled runs |
| Projections (weekly + rest-of-season) | Sleeper projection endpoints, ESPN (unofficial API), candidate: Yahoo's own projected points | Averaged into a consensus, then re-scored with exact Yahoo league scoring |
| ADP | Fantasy Football Calculator free API | Draft value-vs-cost |
| Historical stats / usage (snaps, targets), injuries & practice reports, depth charts | nflverse via `nfl_data_py` (parquet, DuckDB-native) | Free, excellent, canonical |
| Player status + trending adds/drops | Sleeper API | Feeds waiver signal |
| News headlines/blurbs | ESPN news endpoints, RSS | LLM-digested, never directly numeric |
| Player identity crosswalk | nflverse `ff_playerids` | Canonical join spine across yahoo/sleeper/espn/gsis IDs |

Source hygiene: public/free endpoints only, personal use, polite request rates,
every raw pull snapshotted locally so rebuilds don't re-fetch.

## Core engine principles

- **Consensus over cleverness**: average multiple free projection sources;
  re-score to league settings. Own-model projections are a possible season-2
  project, not v1.
- **News informs, numbers decide**: structured status data (injury
  designations, practice participation, depth charts) feeds logic directly;
  narrative news is digested by Claude into per-player flags and prose — it
  never silently adjusts rankings.
- **Deterministic pipeline, LLM at the edges**: all numbers are reproducible
  pure-code; the Claude API writes narrative/digest sections only
  (Sonnet for the weekly brief, Haiku for high-volume news flagging).

## Draft support

**Cheat sheet**: VORP-based values over replacement-level baselines, positional
tiers, scoring-adjusted, ADP alongside for value-vs-cost.

**Draft tracker** — web app on Cloudflare (Workers + D1), mobile-friendly:

- Knows the teams and snake draft order.
- For each pick, prompts for the current team's selection: **3 suggested
  players** (most-likely picks by ADP among those available) plus a name
  autocomplete input; records the pick.
- When it's Brian's turn: recommends the position and player to draft
  (tier- and roster-need-aware).
- Auth: API key entry field on the page, saved in localStorage, sent as a
  bearer token and validated by the Worker.
- **Architecture split**: the Python pipeline computes the board and exports
  static JSON at deploy time; the Cloudflare app is a thin TS layer that
  renders, records picks, and runs simple suggestion logic ported to TS. No
  runtime dependency on the pipeline during the draft.
- Upgrade path (not v1): poll Yahoo's draft endpoint to auto-record picks, if
  a mock draft proves it live-readable.

## Weekly engine

Scope: **lineup + waivers + ROS strategy + trade-offer evaluation** (no
proactive trade-suggestion engine).

- **Sit/start**: optimal lineup from re-scored weekly projections + injury
  status + matchup notes, confidence flags on close calls. Human submits in
  Yahoo — the pipeline never auto-sets lineups.
- **Waivers**: best free agents vs. weakest roster spots, boosted by Sleeper
  trending. (If Yahoo settings reveal FAAB, add bid-size recs later.)
- **ROS strategy**: playoff-weeks schedule strength, stash candidates,
  bye-week planning, buy-low/sell-high flags from usage trends.
- **Trade evaluation**: on-demand, compares rest-of-season value of each side.

### Cadence & delivery

- **Tuesday evening**: the big report — last-week retro, waiver targets
  (before Wednesday processing), ROS strategy.
- **Sunday ~9am ET**: short final lineup check after Saturday injury
  designations, flagging Q/O starters and their replacements.
- **On-demand**: trade eval and "who do I start" via CLI and Claude Code
  sessions (CLAUDE.md will document how to query the DuckDB store directly).
- Reports emailed to bellbm@gmail.com as markdown/HTML briefs and archived in
  `reports/`.

### Feedback loop

Every run snapshots projections and recommendations; actuals land beside them
after games. The Tuesday brief opens with a retro (recommended lineup vs.
actual lineup scores; per-source accuracy). Season-long source accuracy can
later drive consensus weighting.

## Stack & infrastructure

- **Language**: Python, managed with `uv`. TDD for engine logic (scoring,
  VORP, lineup optimizer, suggestions).
- **Storage**: DuckDB single file, all access through one store module; raw
  API snapshots retained alongside.
- **Orchestration**: GitHub Actions cron in a private repo (laptop-
  independent). Secrets: Yahoo tokens, Anthropic key, email creds. DuckDB file
  persisted between runs; Yahoo token refresh persistence handled explicitly.
- **Tracker hosting**: Cloudflare Workers + D1, deployed separately; consumes
  pipeline-exported JSON.
- **LLM**: Claude API inside the pipeline for digest/narrative; Claude Code
  interactively for Q&A.

## Phases

1. **Now → mid-Aug**: foundations — repo + CI, Yahoo auth, ingestion (Sleeper,
   ESPN, ADP, nflverse, crosswalk), DuckDB schema, scoring engine,
   VORP/tiers → cheat sheet.
2. **Mid-Aug → draft**: Cloudflare tracker + board export; dry-run against a
   Yahoo mock draft.
3. **Draft → Week 1**: weekly engine — sit/start, waivers, ROS, retro loop,
   scheduled runs + email.
4. **In-season**: adjustment layer (Vegas lines, matchup strength, usage
   trends), accuracy-driven source weighting, possible push alerts.

## Open items (deferred, small)

- Thursday-night check for TNF players (mini-run or fold into Tuesday report).
- K/DST projection coverage in free sources is spottier — Yahoo's own
  projections are the likely fallback.
- Email sending mechanism (Gmail app-password SMTP vs. a service like Resend).
- Whether Yahoo's draft endpoint updates live (tracker auto-record upgrade).
