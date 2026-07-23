# ffb — fantasy football pipeline

Personal tooling for Brian's Yahoo league: pull free projection sources, store
the raw and normalized data in DuckDB, and compute league-scored,
consensus-ranked output on the command line. See [`DESIGN.md`](DESIGN.md) for the
full design.

Projections from multiple sources (Sleeper, ESPN) are joined through the nflverse
`ff_playerids` **crosswalk** onto a canonical `player_key`, then averaged into a
**consensus** ranking scored to league settings:

```
uv run ffb season sync 2024 --offline
uv run ffb rankings 2024 --position RB --show-sources
```

```
                             2024 rankings — RB
┏━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┳━━━┓
┃ Rank ┃ Player              ┃ Pos ┃ Team ┃ Sleeper ┃  Espn ┃ Consensus ┃ n ┃
┡━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━╇━━━┩
│    1 │ Christian McCaffrey │ RB  │ SFO  │   277.9 │ 335.4 │     306.7 │ 2 │
│    2 │ Breece Hall         │ RB  │ NYJ  │   284.2 │ 289.4 │     286.8 │ 2 │
│  ... │                     │     │      │         │       │           │   │
└──────┴─────────────────────┴─────┴──────┴─────────┴───────┴───────────┴───┘
```

Players are aligned on a canonical `player_key` (nflverse `mfl_id`). Consensus is
the mean of each source's league-scored points; `n` is the source count.
Crosswalk misses remain stored and are reported by `season unmatched`, but they
are excluded from rankings and the draft board.

## Setup

```sh
make init                 # installs Python and tracker dependencies
```

This runs `uv sync` at the repository root and `npm i` in `tracker/`. To install
only the Python package, run `uv sync` directly.

## Usage

```sh
uv run ffb season sync                          # sync 2026, all datasets
uv run ffb season sync 2024 --offline           # replay cached 2024 snapshots
uv run ffb season sync 2026 --source projections
uv run ffb season sync 2026 --refresh           # strict live refresh
uv run ffb season status 2026 --json
uv run ffb season unmatched 2026 --source ffc

uv run ffb rankings 2026                        # persisted consensus ranking
uv run ffb rankings 2026 --position RB
uv run ffb rankings 2026 -p RB --show-sources
uv run ffb rankings 2026 -p WR --limit 40
```

Synchronization is explicit. `season sync` defaults to missing-only behavior:
it replays existing snapshots and fetches only absent datasets. `--refresh`
fetches every selected source, `--offline` prohibits network access, and
`--rebuild` forces cached data through parsing and atomic DB replacement.
Validation prevents an empty or invalid refresh from replacing the last
known-good snapshot or persisted slice. `rankings`, `board show`, and `board
export` never fetch or ingest; they use persisted sources and warn when data is
missing, failed, stale, or untracked.

## Draft cheat sheet

`ffb board` turns the persisted consensus and **ADP** from Fantasy Football
Calculator into a draft board. It computes **VORP** (value over a replacement-level
baseline derived from the league's roster shape) and **positional tiers** over the
league-scored consensus and can export the whole thing.

```sh
uv run ffb board show 2026                       # rich terminal board
uv run ffb board show 2026 -p RB --limit 20      # one position
uv run ffb board export 2026                     # all three files -> exports/
uv run ffb board export 2026 --format json       # board.json only
uv run ffb board export 2026 --output-dir out/
```

The terminal columns are `Rank · Tier · Player · Pos · Team · Bye · Proj · VORP ·
ADP · +/−`, where `+/−` is `adp_rank − rank` (positive = the market drafts them
later than we value them — a value pick). Export always writes the **full**
board to `exports/` (or `--output-dir`, or `$FFB_EXPORT_DIR`); `board.json` is
the versioned, self-contained data contract
the draft tracker consumes — no runtime dependency on this pipeline.

FFC has no id in the crosswalk, so ADP resolves by **normalized name + position**
(with a team tiebreak); ambiguity resolves to *unmatched*, never a guess. Team
defenses use a source-independent `def:<canonical-team>` identity, so Sleeper
and ESPN projections join FFC ADP without relying on `ff_playerids`.

## How it fits together

| Module | Role |
|---|---|
| `sources/sleeper.py` | fetch + parse Sleeper projections |
| `sources/espn.py` | fetch + parse ESPN projections (numeric stat-id decode) |
| `sources/ffc.py` | fetch + parse Fantasy Football Calculator ADP |
| `sources/crosswalk.py` | nflverse `ff_playerids` → canonical identity spine |
| `snapshot.py` | on-disk raw-response cache (offline replay) |
| `store.py` | **the only** module that touches DuckDB |
| `season_data.py` | explicit sync/status/unmatched application service |
| `scoring.py` / `config.py` | pure PPR scoring (computed, never stored) |
| `names.py` | name normalization + `(name, pos)` crosswalk match (pure) |
| `rankings.py` | single-source ranked list |
| `consensus.py` | per-source points pivoted + averaged per player |
| `vorp.py` / `tiers.py` | replacement baselines + largest-gap tiers (pure) |
| `board.py` | consensus ⋈ ADP + VORP + tiers → board rows + serializers (pure) |
| `ingest.py` | snapshot → parse → **resolve to player_key** → store |
| `cli.py` | thin `season`, `rankings`, `board`, and `league` command rendering |

Every source's native id resolves to a canonical `player_key` (nflverse
`mfl_id`) via the crosswalk, so consensus aligns players across sources; misses
fall back to a stored `source:native_id` key for diagnostics and later
self-healing, but do not enter rankings or the draft board.

Points are **computed** from stat lines at read time, not stored — so re-scoring
to different league settings is a config swap, not a re-ingest.

## Development

```sh
uv run pytest              # test suite
uv run ruff check .        # lint
uv run ruff format .       # format
make test-backend-e2e      # offline fixture-to-Worker backend journey
```

Test-driven; CI runs the Python suite, tracker suite, and cross-stack backend E2E
harness independently on every push and PR. The E2E target generates a real
`board.json` from committed fixtures, publishes it to Miniflare KV, applies the
D1 migrations, and exercises the Worker API without live network dependencies.

## Draft tracker (`tracker/`)

[`tracker/`](tracker/) is a **separate TypeScript Cloudflare Worker** that
consumes the pipeline's `board.json` **v1** contract at a file boundary (it never
imports the Python package). On draft day it serves the phone-friendly Draft Room,
records a single manual snake draft, and keeps the available board current. On
Brian’s turn it also derives one explainable recommendation from board VORP,
tiers, roster slots, and Brian’s persisted picks; the usual three ADP-led
“Likely next” choices remain available for every team’s pick.

The board opens in **Available + ALL**. Every row carries an inline tier badge;
choosing a position groups available players under sticky positional tier
headings with live survivor counts. **Drafted** is the complete chronological
pick history, and position filters never regroup that history by tier. Position
and Available/Drafted are independent session-only controls. Draft actions live
in the compact **Pick tools** dock, which starts collapsed and collapses again
after a recorded pick so the board keeps the primary phone viewport.

Architecture: the immutable board blob lives in **KV** (`BOARD`, key
`board:current`) and is served verbatim from `GET /api/board`; live configuration,
ordered teams, and picks live separately in **D1**. The client joins them by
`player.key`, so publishing a new board never changes draft history. Every
`/api/*` request needs `Authorization: Bearer <TRACKER_API_KEY>`; the static shell
is public so the phone can load and enter the key (saved in `localStorage`).

The first authenticated use opens setup: enter 2–20 teams in first-round order,
choose Brian’s team, and set 1–30 rounds. The Worker derives the snake order,
validates the expected pick on each write, snapshots player identity in D1, and
only permits LIFO undo. `DELETE /api/draft` resets the one current draft (picks,
then teams, then configuration) but deliberately leaves the published board in
KV untouched. The other state routes are `GET`/`PUT /api/draft`, `POST /api/picks`,
and `DELETE /api/picks/latest`.

If Yahoo selects someone absent from the board, choose **Record unlisted
player…**, enter their displayed name, position, and optional team, then use the
same separate **Record pick** confirmation. This preserves snake order without
inventing a board key. Before a live mock, use a fresh local draft and verify
recommendations at Brian turns, a snake wheel, after undo, and after an
unlisted-player entry.

```sh
cd tracker
npm ci
npm test                     # vitest + @cloudflare/vitest-pool-workers (offline)
npm run typecheck
npm run build:client         # verify the browser bundle
npx wrangler d1 migrations apply ffb-tracker --local
npm run publish:board        # seed local KV from ../exports/board.json
npm run dev                  # wrangler dev (local KV + D1 via Miniflare)
```

For local `wrangler dev` you need a key: put `TRACKER_API_KEY=<anything>` in
`tracker/.dev.vars` (gitignored). Regenerate the board with `uv run ffb season
sync` followed by `uv run ffb board export` (writes `exports/board.json`), then
`npm run publish:board` to reload the dev store.

### Provisioned Cloudflare deployment (HITL)

The production KV namespace, D1 database, and `ffb.bbell.dev` custom-domain route
are already provisioned. Their IDs are committed in `wrangler.jsonc`; resource
IDs are deployment configuration, not secrets. `TRACKER_API_KEY` remains a
Wrangler secret. Authenticate the machine and set the secret on first deploy
(or rotate it later) from `tracker/`:

```sh
npx wrangler login
npx wrangler secret put TRACKER_API_KEY              # first deploy or key rotation only
```

After that, use the root Makefile for production updates:

```sh
make deploy-board    # refresh sources, export board.json, publish production KV only
make deploy-app      # typecheck/test, apply remote D1 migrations, deploy app/assets
make deploy-all      # deploy the app first, then publish a fresh board
```

Use `deploy-board` for projection, ADP, scoring, or ranking updates that do not
change tracker code. Use `deploy-app` for Worker or browser-app changes; it
applies committed D1 migrations before deploying code that needs them.

Publishing a GitHub Release runs `make deploy-app`, which validates the tracker,
applies remote D1 migrations, and deploys the Worker and static assets. Configure
repository secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` before
publishing a release. Board data is not part of the release workflow; publish it
separately from a development machine with `make deploy-board`.

Then open `https://ffb.bbell.dev` on a phone and enter the key. Apply committed
D1 migrations locally during development as before; local and remote databases
are distinct, so applying `--local` never affects production. Rotate the key any
time with another `wrangler secret put TRACKER_API_KEY` from `tracker/`.

## Fixture-backed league settings

Live Yahoo OAuth is deliberately deferred. To use mock league scoring, roster
shape, team count, and current-week rosters locally, sync an offline fixture:

```sh
uv run ffb league sync 2024 --fixture tests/fixtures/yahoo_league_minimal.json
uv run ffb league show 2024 --rosters
```

Fixture settings are visibly labeled as mock. League state is stored as source
data, while projections, points, VORP, and tiers continue to be derived at read
time. Running `league sync` without `--fixture` explains that live Yahoo support
is pending Task 2b.
