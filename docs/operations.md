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

`--publish` on `lineup`, `digest`, `retro`, and `ros` POSTs the report the
command just printed to the tracker's `/api/inseason/{kind}` route for the
`/command` dashboard. `ffb lineup --league sleeper` is local-only and rejects
`--publish`; it never writes DuckDB `league_*` or Worker KV. `ros --publish` always sends the all-position report and
rejects `-p`. A publish failure prints the Worker's error, exits 1, and leaves
local snapshots untouched. `league sync --from-tracker` pulls the Worker's last
accepted LeagueBundle and imports it exactly as `--fixture` does; it cannot be
combined with `--fixture` or `--refresh`.

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
CLI. Scheduling itself lives outside this repository (`ffb-8yi`).

`make refresh` runs the CLI half of every run below for both leagues after the
Yahoo scrapes are posted. It loads `.env` when present, reads the week from
Sleeper, reports whether each tracker LeagueBundle is from today, runs the
syncs and `--publish` commands, and prints each card's `generated_at`. Pass
`ARGS="--week-roll"` on Wednesday, `ARGS="--sunday"` before kickoff, or
`ARGS="--dry-run"` to preview. Full output lands in `data/refresh-logs/`.

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
| Lineup: N players have no projection | `ffb season sync S --week W --source projections --refresh` → republish lineup |
| News: LLM skipped | set `ANTHROPIC_API_KEY` or `FFB_ANTHROPIC_API_KEY` → `ffb digest S --publish` |
| News: stale | news/injury sync → `ffb digest S --publish` |
| Retro: waiting for actuals | confirm Grok posted `/api/actuals` for week W-1, then `ffb retro S --week W-1 --publish` |
| Retro: not published | `ffb retro S --week W-1 --publish` |
| Retro: no sit/start snapshot (CLI error) | not recoverable for that week; `ffb lineup S --week W-1 --force --publish` writes post-hoc advice, which the retro then grades |
| Rest of season: stale | `ffb season sync S --refresh` → `ffb ros S --publish` |

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
