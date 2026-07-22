# ffb — fantasy football pipeline

Personal tooling for Brian's Yahoo league: pull free projection sources, store
the raw and normalized data in DuckDB, and compute league-scored,
consensus-ranked output on the command line. See [`DESIGN.md`](DESIGN.md) for the
full design.

Projections from multiple sources (Sleeper, ESPN) are joined through the nflverse
`ff_playerids` **crosswalk** onto a canonical `player_key`, then averaged into a
**consensus** ranking scored to league settings:

```
uv run ffb rankings --pos RB --sources
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
Crosswalk misses are never dropped — they rank source-only and are reported.

## Setup

```sh
make init                 # installs Python and tracker dependencies
```

This runs `uv sync` at the repository root and `npm i` in `tracker/`. To install
only the Python package, run `uv sync` directly.

## Usage

```sh
uv run ffb rankings                    # all positions, consensus ranking
uv run ffb rankings --pos RB           # filter position
uv run ffb rankings --pos RB --sources # per-source (Sleeper, ESPN) + consensus
uv run ffb rankings --pos WR --limit 40
uv run ffb rankings --season 2024      # pick season
uv run ffb rankings --pos RB --refresh # re-fetch live from all sources
```

By default runs are **offline**: each raw response (Sleeper, ESPN, the nflverse
crosswalk) is snapshotted under `snapshots/` on first fetch and replayed on later
runs. `--refresh` forces a new network pull and updates the snapshots; validation
keeps a bad crosswalk or FFC response from replacing its last known-good cache.
Without `--sources`, Sleeper is the only projection source fetched (consensus
over one source = that source).

## Draft cheat sheet

`ffb cheatsheet` turns the consensus into a draft board: it ingests **ADP** from
the Fantasy Football Calculator, computes **VORP** (value over a replacement-level
baseline derived from the league's roster shape) and **positional tiers** over the
league-scored consensus, and can export the whole thing.

```sh
uv run ffb cheatsheet                       # rich terminal board
uv run ffb cheatsheet --pos RB --limit 20   # one position
uv run ffb cheatsheet --export              # cheatsheet.md + .csv + board.json -> exports/
uv run ffb cheatsheet --export --export-dir out/   # ...to a chosen dir
uv run ffb cheatsheet --season 2026 --refresh      # live pull once sources publish
```

The terminal columns are `Rank · Tier · Player · Pos · Team · Bye · Proj · VORP ·
ADP · +/−`, where `+/−` is `adp_rank − rank` (positive = the market drafts them
later than we value them — a value pick). `--export` always writes the **full**
board (ignoring `--pos`/`--limit`) to `exports/` (or `--export-dir`, or
`$FFB_EXPORT_DIR`); `board.json` is the versioned, self-contained data contract
the draft tracker consumes — no runtime dependency on this pipeline.

FFC has no id in the crosswalk, so ADP resolves by **normalized name + position**
(with a team tiebreak); ambiguity resolves to *unmatched*, never a guess. Team
defenses aren't in `ff_playerids`, so they ride the board ADP-only and are
reported in the footer.

## How it fits together

| Module | Role |
|---|---|
| `sources/sleeper.py` | fetch + parse Sleeper projections |
| `sources/espn.py` | fetch + parse ESPN projections (numeric stat-id decode) |
| `sources/ffc.py` | fetch + parse Fantasy Football Calculator ADP |
| `sources/crosswalk.py` | nflverse `ff_playerids` → canonical identity spine |
| `snapshot.py` | on-disk raw-response cache (offline replay) |
| `store.py` | **the only** module that touches DuckDB |
| `scoring.py` / `config.py` | pure PPR scoring (computed, never stored) |
| `names.py` | name normalization + `(name, pos)` crosswalk match (pure) |
| `rankings.py` | single-source ranked list |
| `consensus.py` | per-source points pivoted + averaged per player |
| `vorp.py` / `tiers.py` | replacement baselines + largest-gap tiers (pure) |
| `board.py` | consensus ⋈ ADP + VORP + tiers → board rows + serializers (pure) |
| `ingest.py` | snapshot → parse → **resolve to player_key** → store |
| `cli.py` | `ffb rankings` / `ffb cheatsheet`, rich table output |

Every source's native id resolves to a canonical `player_key` (nflverse
`mfl_id`) via the crosswalk, so consensus aligns players across sources; misses
fall back to a `source:native_id` key and are reported, never dropped.

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
`tracker/.dev.vars` (gitignored). Regenerate the board with `uv run ffb
cheatsheet --export` (writes `exports/board.json`), then `npm run publish:board`
to reload the dev store.

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

Then open `https://ffb.bbell.dev` on a phone and enter the key. Apply committed
D1 migrations locally during development as before; local and remote databases
are distinct, so applying `--local` never affects production. Rotate the key any
time with another `wrangler secret put TRACKER_API_KEY` from `tracker/`.

## Fixture-backed league settings

Live Yahoo OAuth is deliberately deferred. To use mock league scoring, roster
shape, team count, and current-week rosters locally, sync an offline fixture:

```sh
uv run ffb league sync --season 2024 --fixture tests/fixtures/yahoo_league_minimal.json
uv run ffb league show --season 2024 --rosters
```

Fixture settings are visibly labeled as mock. League state is stored as source
data, while projections, points, VORP, and tiers continue to be derived at read
time. Running `league sync` without `--fixture` explains that live Yahoo support
is pending Task 2b.
