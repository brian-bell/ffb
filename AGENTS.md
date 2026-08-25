# AGENTS.md — ffb

Personal fantasy-football pipeline for Brian's Yahoo league. The Python CLI
snapshots free data sources, normalizes them into DuckDB, and computes
league-scored rankings plus a VORP/tier/ADP draft board. `tracker/` is an
independent TypeScript Cloudflare Worker that consumes exported `board.json` v1
and stores live and mock draft state in D1.

Read [docs/architecture.md](docs/architecture.md) before changing a boundary,
[docs/data-sources.md](docs/data-sources.md) before changing ingestion, and
[docs/tracker.md](docs/tracker.md) for tracker contracts and invariants.

## Build, test, and run

```sh
make init                         # uv sync + npm install in tracker/

uv run ffb season sync 2026 --refresh
uv run ffb season status 2026
uv run ffb rankings 2026 -p RB --show-sources
uv run ffb board show 2026
uv run ffb board export 2026

uv run pytest
uv run ruff check .
uv run ruff format --check .
make test-backend-e2e
```

Tracker commands use its pinned Node 24.18.0/npm 11.16.0 toolchain:

```sh
cd tracker
nvm use
npm ci
npm run typecheck
npm test
npm run test:browser
npm run build:client
```

Run `make test-backend-e2e` for changes that can affect ingestion fixtures,
season synchronization, CLI board export, `board.json`, D1 migrations, KV/D1
behavior, or Worker APIs. The test is offline and uses isolated temporary state.
See [docs/operations.md](docs/operations.md) for deploy and recovery procedures.

## Layout and boundaries

```text
src/ffb/          Python package and CLI
  cli.py          thin Typer rendering
  season_data.py  sync/status/unmatched orchestration
  ingest.py       snapshot → parse → identity resolution → store
  store.py        only DuckDB gateway
  sources/        thin fetch + pure parse modules
  scoring.py      pure configurable scoring
  consensus.py    cross-source league-scored average
  board.py        ADP/byes/VORP/tiers → board serializers
  identity.py     canonical teams and DEF/DST identities
  names.py        normalized name matching for FFC
tests/            deterministic pytest suite and committed API fixtures
tracker/          separate Worker, client, migrations, and Vitest/Playwright tests
docs/             durable architecture, source, tracker, and operations guides
```

The main dependency paths are:

```text
CLI writes: cli → season_data → ingest → store
CLI reads:  cli → consensus/board → store + pure compute
Tracker:    board.json → KV → Worker/client; draft state → D1
```

## Invariants

- Only `src/ffb/store.py` imports `duckdb`; `test_layering.py` enforces the
  I/O-free compute boundaries.
- Points, consensus, VORP, and tiers are computed at read time. Raw source
  values such as projections, ADP, and team byes are stored.
- `season sync` is the only projection/ADP/schedule ingest path. Rankings and
  board commands are read-only and never fetch.
- Every raw pull is cached under `snapshots/`. Refreshes validate before
  replacing known-good snapshots or database slices.
- Players resolve through nflverse `mfl_id`; defenses use
  `def:<canonical-team>`. Unresolved rows remain stored with `matched=false` for
  diagnostics but do not enter rankings or the board.
- FFC has no crosswalk id, so ADP matches by normalized `(name, position)` with
  a team tiebreak. Ambiguity remains unmatched; never guess.
- Projection parsers admit only `QB/RB/WR/TE/K/DEF`. A matched crosswalk row
  cannot reintroduce a disallowed position.
- Draftability is provider evidence stored on projection rows and applied only
  when the board is read. The board defaults to `draftable`; `all` is the
  matched diagnostic pool. Filter before byes, VORP, tiers, and ranks.
- Schedule-derived byes win over FFC's fallback bye. Schedule ingest requires
  all canonical NFL teams before replacing the stored mirror.
- `board.json` is a self-contained versioned contract. Breaking envelope or
  player-shape changes require a version bump and coordinated tracker changes.
- The tracker never imports Python. Live and mock state are separate; mock code
  uses only `mock_*` tables, immutable board snapshots, and monotonic revisions.
- Preserve existing routes, output shapes, and user-visible behavior unless the
  task explicitly changes them.

## Working conventions

- Use TDD for behavior changes, bug fixes, parsers, and public interfaces.
- Before editing, inspect branch and worktree. Preserve user changes, update
  from `main` when safe, and work on a branch; never commit directly to `main`.
- Make the smallest coherent change. Do not perform unrelated cleanup.
- Python dependency changes must update `uv.lock`. Tracker dependency changes
  must run under the pinned Node/npm versions and update
  `tracker/package-lock.json`.
- Schema changes do not migrate existing DuckDB files. The database is a
  disposable cache: move it aside and replay snapshots with `season sync
  SEASON --offline --rebuild`.
- `league sync` without `--fixture` uses the live Yahoo adapter
  (`sources/yahoo.py` + `yahoo_auth.py`), configured via `FFB_YAHOO_*` env
  vars; it is inert until the one-time browser authorization (ffb-1ct.2)
  stores a token. Fixture mode remains, and the confirmed 10-team Yahoo
  config is the fallback. Tokens and secrets must never reach logs, DuckDB,
  fixtures, or snapshots.
- ESPN and FFC endpoints are unofficial. Keep parsers defensive and fixtures
  representative; live drift is checked only through an explicit refresh.

## Verification

Run the smallest relevant tests first, then the broader gates proportional to
risk. CI runs:

- `uv sync --frozen`, Ruff check/format check, and pytest;
- tracker `npm ci`, typecheck, Vitest, and Chromium viewport tests;
- `make test-backend-e2e` across the Python/Worker boundary.

Report commands and results, including checks that could not run. Before
finishing Beads-tracked work, close completed issues, run quality gates, inspect
`git status`, and follow the conservative profile: do not commit, push, or sync
Beads remotely without explicit authority.

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking. Do not create Markdown TODO lists or
ad hoc memory files.

```sh
bd prime
bd ready
bd show <id>
bd update <id> --claim
bd close <id>
```

Keep persistent project knowledge with `bd remember`. Issue data lives in the
local Dolt database and syncs through `refs/dolt/data`; `.beads/issues.jsonl` is
a passive export.
<!-- END BEADS CODEX SETUP -->
