# ffb

Personal fantasy-football tooling for Brian's Yahoo league. The Python CLI
downloads free projection, ADP, schedule, and identity data; snapshots each raw
response; stores normalized rows in DuckDB; and produces league-scored rankings
and a draft board. A separate Cloudflare Worker in `tracker/` consumes the
exported `board.json` for live and mock drafts.

The current Yahoo integration is fixture-backed. Live Yahoo OAuth and weekly
management remain planned work; see [DESIGN.md](DESIGN.md) for product scope.

## Requirements and setup

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- Node 24.18.0 and npm 11.16.0 for `tracker/` (`nvm use` applies the pins)

```sh
make init
```

`make init` installs both toolchains. For the Python package only, run `uv sync`.

## Quick start

Synchronize a season explicitly, then read the persisted data:

```sh
uv run ffb season sync 2026 --refresh
uv run ffb season status 2026
uv run ffb rankings 2026 -p RB --show-sources
uv run ffb board show 2026
uv run ffb board export 2026
```

`season sync` is the only projection, ADP, schedule, and crosswalk ingestion
path. Its default policy reuses existing snapshots and fetches only missing
ones. Use `--refresh` to fetch selected sources again, `--offline` to prohibit
network access, and `--rebuild` to force cached data back through parsing and
atomic replacement.

Read commands never fetch. They warn about missing, failed, stale, or untracked
inputs and use whatever valid projection sources are persisted. Inspect misses
with:

```sh
uv run ffb season unmatched 2026
uv run ffb season unmatched 2026 --source ffc
```

The draft board combines league-scored projection consensus with Fantasy
Football Calculator ADP, schedule-derived bye weeks, VORP, and positional tiers.
It defaults to players that current source evidence marks as draftable; use
`--player-pool all` for the complete matched diagnostic pool. Export writes
Markdown, CSV, and the self-contained `board.json` v1 contract to `exports/`.

## League settings

Until the live Yahoo adapter exists, league scoring, roster shape, teams, and
current-week rosters can be loaded from a validated fixture:

```sh
uv run ffb league sync 2026 --fixture tests/fixtures/yahoo_league_minimal.json
uv run ffb league show 2026 --rosters
```

Without stored league state, the CLI uses the confirmed 10-team Yahoo fallback
in `src/ffb/config.py`.

## Draft tracker

The tracker is a separate TypeScript Cloudflare Worker with its own dependencies
and tests. It reads `board.json` from KV, stores live and isolated mock-draft
state in D1, and has no runtime dependency on Python.

```sh
cd tracker
nvm use
npm ci
npm run typecheck
npm test
npm run test:browser
npm run dev
```

Local development needs `TRACKER_API_KEY=<anything>` in the gitignored
`tracker/.dev.vars`. Export a board first, then run `npm run publish:board` to
seed local KV. See [docs/tracker.md](docs/tracker.md) for behavior and API
details and [docs/operations.md](docs/operations.md) for local and production
workflows.

## Development

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .

cd tracker
npm run typecheck
npm test
npm run test:browser

# Cross-stack fixture → CLI → DuckDB/board → Worker KV/D1 journey
cd ..
make test-backend-e2e
```

CI runs the Python, tracker, and backend end-to-end suites independently and
without live data sources.

## Documentation

- [Architecture](docs/architecture.md) — boundaries, data flow, identity,
  storage, and board computation
- [Data sources](docs/data-sources.md) — endpoints, parsing, snapshots, and
  source-specific gotchas
- [Draft tracker](docs/tracker.md) — live and mock behavior, APIs, persistence,
  and UI invariants
- [Operations](docs/operations.md) — rebuilds, environment overrides, testing,
  publishing, and deployment
- [Design](DESIGN.md) — product direction and deferred weekly-management scope
