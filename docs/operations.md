# Operations

This guide covers local data refresh, cache recovery, tracker development, and
production deployment. The pipeline never writes back to projection providers
or Yahoo.

## Data locations

Defaults are relative to the repository root:

| Data | Default | Override |
| --- | --- | --- |
| DuckDB cache | `data/ffb.duckdb` | `FFB_DB_PATH` |
| Raw snapshots | `snapshots/` | `FFB_SNAPSHOT_DIR` |
| Board exports | `exports/` | `FFB_EXPORT_DIR` |

All three default locations are gitignored. Tests use committed fixtures and
temporary directories instead of this operator state.

## Synchronizing season data

```sh
uv run ffb season sync 2026                 # missing-only
uv run ffb season sync 2026 --refresh       # fetch all selected sources
uv run ffb season sync 2026 --offline       # cached snapshots only
uv run ffb season sync 2026 --offline --rebuild
uv run ffb season sync 2026 --source projections
uv run ffb season sync 2026 --week 1 --source projections
uv run ffb lineup 2026
FFB_SLEEPER_LEAGUE_ID=1395854363380965376 \
FFB_SLEEPER_USER_ID=1395866680286003200 \
  uv run ffb lineup 2026 --league sleeper
uv run ffb retro 2026 --week 1 --fixture PATH
uv run ffb lineup 2026 --force              # replace the locked sit/start snapshot
uv run ffb retro 2026 --week 1 --fixture PATH --force   # replace locked actuals
FFB_TRACKER_URL=https://<worker> FFB_TRACKER_API_KEY=<key> uv run ffb retro 2026 --week 1
uv run ffb ros 2026
uv run ffb season sync 2026 --source ffc
uv run ffb season sync 2026 --source schedule
uv run ffb season sync 2026 --source injuries
uv run ffb season sync 2026 --source news
uv run ffb digest 2026
uv run ffb season status 2026 --json
FFB_TRACKER_URL=https://<worker> FFB_TRACKER_API_KEY=<key> uv run ffb league sync 2026 --from-tracker
FFB_TRACKER_URL=https://<worker> FFB_TRACKER_API_KEY=<key> uv run ffb lineup 2026 --publish
```

`--publish` on `lineup`, `digest`, `retro`, `ros`, and `waivers` POSTs the report the
command just printed to the tracker's `/api/inseason/{kind}` route for the
`/command` dashboard. `ffb lineup --league sleeper` is local-only and rejects
`--publish`; it never writes DuckDB `league_*` or Worker KV. `ros --publish` always sends the all-position report and
rejects `-p`. `ffb waivers` on a Sleeper league reads the league sync. On a Yahoo league it
ranks the free-agent pool the Grok bot POSTed to `/api/free-agents`: pass
`--from-tracker` to pull it (or `--fixture PATH`); with neither it reuses the
last snapshot and exits 1 if there is none. A pool from another league is
rejected, and `--publish` refuses a pool scraped more than 5 days ago. A publish failure prints the Worker's error, exits 1, and leaves
local snapshots untouched. `league sync --from-tracker` pulls the Worker's last
accepted LeagueBundle and imports it exactly as `--fixture` does; it cannot be
combined with `--fixture` or `--refresh`.

`league sync S --league sleeper --push` runs the live Sleeper sync, then POSTs
the bundle it just imported to `/api/league/bundle?league=sleeper:<id>`. It
needs `FFB_TRACKER_URL` and `FFB_TRACKER_API_KEY` before it fetches, and it
rejects `--offline`, `--week`, `--fixture`, and `--from-tracker` because a
replayed or backfilled bundle must not replace the current one. A 409
`stale_bundle` means the Worker already holds a newer bundle; the command
prints `Not published` and exits 0. Any other rejection exits 1 after the
local import.

Every selected source is attempted and recorded independently. Validation keeps
an invalid or empty response from replacing a known-good snapshot or persisted
slice. `--verbose` logs cache decisions, safe request summaries, and processing
progress to stderr without headers or response bodies.

Read commands use persisted data only. Run an explicit refresh before export
when fresh projection, ADP, schedule, or injury data is required. Sleeper's
full player map is capped at one provider attempt per 24 hours. A healthy young
snapshot is replayed under `--refresh`. A corrupt or truncated cache can make
one guarded recovery attempt, but an accepted or rejected attempt suppresses
another call until the global 24-hour window expires. The attempt is claimed
under a process-safe local lock before the network call, so concurrent sync
processes share the same limit. The lock is released after the durable claim;
a crashed process cannot retain it. If the marker is up to 24 hours ahead of
the sync clock, the cache treats the difference as clock rollback, throttles
the call, and does not move the marker backward. A marker more than 24 hours
ahead is treated as corrupt. One claimant repairs it to the current attempt
time and proceeds while concurrent or subsequent claimants are throttled. This
provider-call marker is separate from accepted-snapshot freshness and never
makes rejected data current.

## In-season refresh runbook

Two scheduled runs per week feed the command center, both driven by the Grok
bot. Grok scrapes Yahoo and posts to the two existing Worker routes, then runs
the CLI from the repository root with `FFB_TRACKER_URL` and
`FFB_TRACKER_API_KEY` set. `S` is the season and `W` the current week. Grok
never runs `season sync` against Yahoo; the free sources are fetched by the
CLI. Scheduling itself lives outside this repository; the Grok bot's cadence is the
accepted practice, with no email delivery or `reports/` archive.

`make refresh` runs the CLI half of every run below for both leagues after the
Yahoo scrapes are posted. It loads `.env` when present, reads the week from
Sleeper, reports whether each tracker LeagueBundle is from today, runs the
syncs and `--publish` commands, and prints each card's `generated_at`. When
the Sleeper bundle is `STALE` or missing, its league sync adds `--push`, so
`/api/leagues` shows today's Sleeper `synced_at` without a manual step. A
stale or missing Yahoo bundle still needs the Yahoo scrape: the run finishes
the season sync and every Sleeper step, skips the Yahoo league sync and Yahoo
cards, and ends with a `next:` line naming the exact rerun. The script exits 3
in that case; `make refresh` cannot forward that code, so it exits 0 and the
`next:` line is the signal. After the `yahoo-league-bundle` skill posts the
bundle, that rerun (`make refresh ARGS="--skip-sync --yahoo-only --week W"`)
publishes only the three Yahoo cards. `--allow-stale` publishes Yahoo cards
from the stale bundle instead. Pass `ARGS="--week-roll"` on Wednesday, `ARGS="--sunday"` before
kickoff, or `ARGS="--dry-run"` to preview; a dry run lists skipped steps as
`would skip:`. Full output lands in `data/refresh-logs/`.
The daily Wed–Sun job that drives `make refresh`, including the Yahoo actuals
scrape and stale-bundle handling, is documented step by step in
[refresh-job.md](refresh-job.md).

**Wednesday 10:00 ET, week roll.** Mid-morning gives Yahoo slack to roll the
week and apply Monday's stat corrections.

1. Grok posts the week `W-1` `WeeklyActualsBundle` to `POST /api/actuals`.
2. Grok posts the week `W` `LeagueBundle` to `POST /api/league/bundle`.
3. Grok runs, in order:

```sh
ffb retro S --week W-1 --publish
ffb season sync S --week W --refresh
ffb league sync S --from-tracker
ffb ros S --publish
ffb lineup S --week W --publish
ffb digest S --publish
```

Retro runs first: it pulls actuals from the Worker and grades the locked `W-1`
snapshot. If the actuals scrape lags, retry retro alone later. Lineup runs
after league sync and writes the week `W` snapshot.

Sleeper week roll, same window, after the Yahoo commands. It does not scrape
Yahoo. `league sync --week` caches Sleeper `/matchups` (that fetch is live).
`retro --from-matchups` maps the cache offline and `--publish` posts the retro
envelope to the Sleeper slot. `retro --league sleeper` without
`--from-matchups` instead pulls `GET /api/actuals?league=sleeper:…`, so a
posted Sleeper bundle grades the same way Yahoo's does. Retro does not itself
call Sleeper, and `--publish` does not POST the actuals bundle.

```sh
ffb league sync S --league sleeper --week W-1
ffb retro S --week W-1 --league sleeper --from-matchups --publish
```

`make refresh` also publishes `ffb waivers S --league sleeper --publish` after
the Sleeper cards on every run, so the waiver card tracks the latest rosters.
It does not publish Yahoo waivers: Grok posts the Yahoo free-agent pool and
runs `ffb waivers S --from-tracker --publish` itself, after the Yahoo league
sync, on both scheduled runs.

**Sunday 07:00 ET, pre-kickoff refresh.** Early enough to act before the
9:30 AM ET international games; Friday and Saturday injury designations are
already in.

1. Grok posts the week `W` `LeagueBundle` to `POST /api/league/bundle`.
2. Grok runs, in order:

```sh
ffb season sync S --week W --source projections --source injuries --source news --refresh
ffb league sync S --from-tracker
ffb lineup S --week W --force --publish
ffb digest S --publish
```

`--force` replaces the Wednesday snapshot so retro grades the last advice that
was actionable before kickoff. Thursday night players get Wednesday's advice;
Sunday inactives (90 minutes before each kickoff) are an accepted gap. No Thursday, Monday, or daily
news runs are scheduled; the 5-day and 8-day freshness limits match this
cadence.

Card-by-card recovery when a badge is not green:

| Badge | Fix |
| --- | --- |
| Lineup: roster changed | `ffb league sync S --from-tracker` → `ffb lineup S --week W --force --publish` |
| Lineup: newer injury report | `ffb season sync S --source injuries --refresh` → `ffb lineup S --week W --force --publish` |
| Lineup: N players have no projection | `ffb season sync S --week W --source projections --refresh` → republish lineup. The same sync refreshes the crosswalk and fills a null `sleeper_id` from the cached Sleeper player map when a unique external id links the row. That player-map snapshot comes from an injuries sync; a projections-only command does not refetch it. |
| News: LLM skipped | set `ANTHROPIC_API_KEY` or `FFB_ANTHROPIC_API_KEY` → `ffb digest S --publish` |
| News: stale | news/injury sync → `ffb digest S --publish` |
| Retro: waiting for actuals | confirm Grok posted `/api/actuals` for week W-1, then `ffb retro S --week W-1 --publish` |
| Retro: not published | `ffb retro S --week W-1 --publish` |
| Retro: no sit/start snapshot (CLI error) | not recoverable for that week; `ffb lineup S --week W-1 --force --publish` writes post-hoc advice, which the retro then grades |
| Rest of season: stale | `ffb season sync S --refresh` → `ffb ros S --publish` |
| Waivers: rosters changed or stale | `ffb league sync S --league sleeper` → `ffb waivers S --league sleeper --publish` |
| Waivers (Yahoo): not published or stale | Grok re-scrapes and POSTs `/api/free-agents`, then `ffb waivers S --from-tracker --publish` |
| Waivers: N rostered players are unmatched | `ffb season sync S --refresh` → republish waivers |

## Rebuilding DuckDB

The store uses `CREATE TABLE IF NOT EXISTS` and intentionally has no migration
framework. If the schema changes, move the disposable database aside and replay
cached snapshots:

```sh
mv data/ffb.duckdb data/ffb.duckdb.bak
uv run ffb season sync 2026 --offline --rebuild
```

The store checks expected columns at open time and reports this recovery path
instead of failing later in a query. Content-only parser or normalization
changes may also require a rebuild even when columns are unchanged.

`schedule_games` gained nullable `gameday` and `gametime`. A database from
before those columns fails at open with that same move-aside message and is
not altered in place. `--offline --rebuild` replays the cached schedule,
which for 2026 has no kickoff times until one refresh:

```sh
mv data/ffb.duckdb data/ffb.duckdb.bak
uv run ffb season sync 2026 --offline --rebuild
uv run ffb season sync 2026 --source schedule --refresh
```

A schedule `--refresh` is rejected, and the old snapshot kept, when the
payload omits `gameday` or `gametime`, has no parseable kickoff, or has fewer
timed games than the cache. `--refresh` replaces a truncated or malformed
schedule file. Without `--refresh`, that file still raises.

## Retro history and calibration backtest

`ffb retro history` and `ffb calibrate backtest` score the newest ledger run
in each league-week, then each player's last prediction from before kickoff.
They drop, and the report notes:

- a prediction at or after kickoff when no earlier pre-kickoff row remains,
  and any player with no known kickoff. With no kickoff times at all the note
  is `no kickoff times available; predictions were not scored`
- an advice week that is not paired to the scored ledger:
  `no decision metrics: advice snapshot is not paired to the scored ledger`
- a mixed-run week (Thursday and Sunday players taken from different runs):
  `no decision metrics or league Brier: scored predictions do not share the advice ledger`

Source accuracy and the calibration fit still use the pre-kickoff rows. The
live lineup fit uses that same selection.

## One-time 2024–2025 prior backfill

`ffb calibrate priors` snapshots past-season weekly projections and nflverse
player stats so the lineup fit and `ffb calibrate backtest` can train on them.
It writes snapshots only. It does not write DuckDB, call Yahoo, or publish.
The daily refresh job does not run it. Run it once, after a Sleeper league
sync if Sleeper priors should be scored:

```sh
uv run ffb league sync 2026 --league sleeper
uv run ffb calibrate priors
uv run ffb calibrate waivers 2024 --week 1
```

`--season` repeats (the default is 2024 and 2025, weeks 1–18). `--offline`
replays the cache. `--refresh` fetches those keys again and replaces a file
only when its parser returns at least one usable row. A nonempty payload that
parses to nothing is rejected, the known-good snapshot stays, and the command
prints that and exits 1. Other seasons still complete. Re-running without
`--refresh` fetches only what is missing.

Sleeper's past-week endpoint does return per-player weekly forecasts. Its
`updated_at` is a later storage rewrite, often after kickoff, so prior rows
use `generated_at` `prior` and are kept only when that season's schedule has
a kickoff for the team-week. `hours_before_kickoff` stays null. Details and
the third-source evaluation are in
[retro-feedback-loop.md](specs/retro-feedback-loop.md).

`ffb calibrate waivers SEASON --week N` grades free agents from those priors.
It defaults to `sleeper:1395854363380965376` and exits 1 for Yahoo. It needs
that season's snapshots and Sleeper scoring in the store.

## Local tracker

```sh
uv run ffb board export 2026

cd tracker
nvm use
npm ci
npx wrangler d1 migrations apply ffb-tracker --local
npm run publish:board
npm run dev
```

Put `TRACKER_API_KEY=<anything>` in gitignored `tracker/.dev.vars`. Local D1 and
KV are Miniflare state and are separate from production. Re-export the board and
rerun `npm run publish:board` after local data changes. To fill the local
`/command` page, point `FFB_TRACKER_URL` at the dev server and run the report
commands with `--publish`.

## Production resources and secrets

`tracker/wrangler.jsonc` contains the provisioned KV namespace, D1 database, and
`ffb.bbell.dev` custom-domain route. Those resource identifiers are non-secret
deployment configuration. `TRACKER_API_KEY` is a Wrangler secret:

```sh
cd tracker
npx wrangler login
npx wrangler secret put TRACKER_API_KEY
```

The GitHub release workflow additionally needs repository secrets
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.

## Deploying

Use the root Makefile:

```sh
uv run ffb season sync 2026 --refresh  # only when fresh inputs are needed
make deploy-board SEASON=2026
make deploy-app
make deploy-all SEASON=2026
```

`deploy-board` is data-only. It exports the persisted default draftable pool,
refuses an empty board, requires at least `MIN_BOARD_PLAYERS` players (100 by
default), and publishes `exports/board.json` to production KV without deploying
code.

`deploy-app` runs tracker type checking and Vitest, applies pending remote D1
migrations, then builds and deploys the Worker and static assets. `deploy-all`
deploys the application first and then exports and publishes board data.

Publishing a GitHub Release runs `make deploy-app` against the release tag.
Release deployment never publishes board data; update the board separately from
an authenticated development machine.

## Validation matrix

| Change | Minimum relevant checks |
| --- | --- |
| Python parsing, scoring, identity, store, or CLI | Targeted pytest, full pytest, Ruff check and format check |
| Tracker domain, API, store, or client | Typecheck, Vitest, client build |
| Responsive tracker UI | Typecheck, Vitest, client build, Playwright viewport suite |
| Ingestion fixture, board export/contract, D1, KV, or Worker API | All relevant local checks plus `make test-backend-e2e` |
| In-season publish envelope, `/api/inseason`, or `/command` | Targeted pytest, tracker typecheck, Vitest, Playwright `command.spec.ts`, and `make test-backend-e2e` |
| Tracker dependency or lockfile | Run under `nvm use`; commit `tracker/package-lock.json` |
| Python dependency or lockfile | Update `uv.lock`; verify `uv sync --frozen` |

The backend end-to-end harness generates a real board from committed snapshots,
publishes it to isolated KV, applies D1 migrations, and exercises the Worker API
without live network or Cloudflare dependencies. It also runs the four report
commands with `--publish` against a local capture server and replays the
captured envelopes through the real `/api/inseason` routes. Set `FFB_E2E_KEEP_TMP=1` to
retain temporary state while diagnosing a failure.
