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
uv run ffb season sync 2026 --source ffc
uv run ffb season sync 2026 --source schedule
uv run ffb season status 2026 --json
```

Every selected source is attempted and recorded independently. Validation keeps
an invalid or empty response from replacing a known-good snapshot or persisted
slice. `--verbose` logs cache decisions, safe request summaries, and processing
progress to stderr without headers or response bodies.

Read commands use persisted data only. Run an explicit refresh before export
when fresh projection, ADP, or schedule data is required.

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
rerun `npm run publish:board` after local data changes.

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
| Tracker dependency or lockfile | Run under `nvm use`; commit `tracker/package-lock.json` |
| Python dependency or lockfile | Update `uv.lock`; verify `uv sync --frozen` |

The backend end-to-end harness generates a real board from committed snapshots,
publishes it to isolated KV, applies D1 migrations, and exercises the Worker API
without live network or Cloudflare dependencies. Set `FFB_E2E_KEEP_TMP=1` to
retain temporary state while diagnosing a failure.
