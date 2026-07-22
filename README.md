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
uv sync
```

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
runs. `--refresh` forces a new network pull and overwrites the snapshots. Without
`--sources`, only Sleeper is fetched (consensus over one source = that source).

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
```

Test-driven; CI runs lint + format + tests on every push and PR.

## Draft tracker (`tracker/`)

[`tracker/`](tracker/) is a **separate TypeScript Cloudflare Worker** that
consumes the pipeline's `board.json` **v1** contract at a file boundary (it never
imports the Python package). On draft day it serves the phone-friendly Draft Room,
records a single manual snake draft, and keeps the available board current.

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

```sh
cd tracker
npm install
npm test                     # vitest + @cloudflare/vitest-pool-workers (offline)
npm run typecheck
npm run dev                  # wrangler dev (local KV + D1 via Miniflare)
npm run publish:board        # seed the LOCAL dev KV from ../exports/board.json
npx wrangler d1 migrations apply ffb-tracker --local
```

For local `wrangler dev` you need a key: put `TRACKER_API_KEY=<anything>` in
`tracker/.dev.vars` (gitignored). Regenerate the board with `uv run ffb
cheatsheet --export` (writes `exports/board.json`), then `npm run publish:board`
to reload the dev store.

### One-time Cloudflare setup (HITL)

The KV/D1 ids in `wrangler.jsonc` are **placeholders**; they are not secrets and
are filled in once. Run from `tracker/`:

```sh
npx wrangler login
npx wrangler kv namespace create BOARD               # → id      → wrangler.jsonc
npx wrangler kv namespace create BOARD --preview     # → preview_id → wrangler.jsonc
npx wrangler d1 create ffb-tracker                   # → database_id → wrangler.jsonc
npx wrangler d1 migrations apply ffb-tracker --remote
npx wrangler secret put TRACKER_API_KEY              # the shared key (never committed)
npm run deploy                                       # note the *.workers.dev URL
npm run publish:board:remote                         # load the live board into prod KV
```

Then open the URL on a phone, enter the key, and the board renders by tier.
Rotate the key any time with another `wrangler secret put TRACKER_API_KEY`.

Apply the committed D1 migrations separately to production before the first
draft: `npx wrangler d1 migrations apply ffb-tracker --remote`. Local and remote
databases are distinct; applying `--local` does not affect production.
