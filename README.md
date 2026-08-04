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
uv run ffb season sync 2026 --refresh --verbose # trace API + processing steps
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
`-v`/`--verbose` writes cache decisions, safe API request summaries, and
parse/resolve/store progress to stderr; headers and response bodies are never
logged. A cache-hit message means that source did not make a network call.
Validation prevents an empty or invalid refresh from replacing the last
known-good snapshot or persisted slice. `rankings`, `board show`, and `board
export` never fetch or ingest; they use persisted sources and warn when data is
missing, failed, stale, or untracked.

## Draft cheat sheet

`ffb board` turns the persisted consensus and **ADP** from Fantasy Football
Calculator into a draft board. It computes **VORP** (value over a replacement-level
baseline derived from the league's roster shape) and **positional tiers** over the
league-scored consensus and exports the active, draftable pool by default.

```sh
uv run ffb board show 2026                       # rich terminal board
uv run ffb board show 2026 -p RB --limit 20      # one position
uv run ffb board show 2026 --player-pool all     # full diagnostic pool
uv run ffb board export 2026                     # all three files -> exports/
uv run ffb board export 2026 --format json       # board.json only
uv run ffb board export 2026 --output-dir out/
```

The terminal columns are `Rank · Tier · Player · Pos · Team · Bye · Proj · VORP ·
ADP · +/−`, where `+/−` is `adp_rank − rank` (positive = the market drafts them
later than we value them — a value pick). Show and export select the
**draftable** player pool unless `--player-pool all` requests the former full
matched-player pool for diagnostics. Export writes the selected board to
`exports/` (or `--output-dir`, or `$FFB_EXPORT_DIR`); `board.json` remains the
version-1, self-contained data contract the draft tracker consumes, with no
player-field or envelope change and no runtime dependency on this pipeline.

Draftability comes from the raw provider evidence captured before canonical
identity can replace a source team. A Sleeper row is draftable when its raw
`player.team` resolves to a current NFL team. An ESPN row requires both
`active: true` and a `proTeamId` mapped to a current NFL team. A consensus player
is retained when any contributing projection source is affirmative; projection
evidence is authoritative, so ADP cannot rescue a player whose contributing
projection sources are all negative or unknown. A matched ADP-only row requires
a valid current FFC team. Team defenses follow the same current-team rule, and
missing or unknown evidence is non-draftable.

Here, “free agent” means an unsigned NFL player, not someone available on Yahoo
waivers. Injury labels alone do not remove a player: Questionable, IR, PUP, and
suspended players remain eligible when the provider still reports the required
activity and team assignment. Selection happens only while the board is read,
before bye attachment, VORP, tiers, and ranks are computed. Raw snapshots,
normalized projections, rankings, season status, and unmatched diagnostics keep
both draftable and non-draftable rows.

Draftability is source evidence, so it is stored, and `data/ffb.duckdb` carries
a column that databases written by earlier versions lack. The store is a
disposable cache rather than a migrated database: move such a file aside and
replay its cached snapshots.

```sh
mv data/ffb.duckdb data/ffb.duckdb.bak
uv run ffb season sync 2026 --offline --rebuild
```

Forgetting to do so is safe: every command opens the store through a schema
check that compares the file against the current column set and stops with those
two commands in the message, instead of failing later inside a query.

FFC has no id in the crosswalk, so ADP resolves by **normalized name + position**
(with a team tiebreak); ambiguity resolves to *unmatched*, never a guess. Team
defenses use a source-independent `def:<canonical-team>` identity, so Sleeper
and ESPN projections join FFC ADP without relying on `ff_playerids`.

**Bye weeks come from the nflverse season schedule**, not from ADP: the
`schedule` source derives one bye per team from the published regular-season
schedule, and the board joins byes by canonical team code for players, kickers,
and D/ST alike. FFC's own `bye` field is only a fallback, so a player missing
from FFC's (non-exhaustive) list keeps their bye even without an ADP.

## How it fits together

| Module | Role |
|---|---|
| `sources/sleeper.py` | fetch + parse Sleeper projections |
| `sources/espn.py` | fetch + parse ESPN projections (numeric stat-id decode) |
| `sources/ffc.py` | fetch + parse Fantasy Football Calculator ADP |
| `sources/schedule.py` | fetch + parse nflverse schedule → team bye weeks |
| `sources/crosswalk.py` | nflverse `ff_playerids` → canonical identity spine |
| `snapshot.py` | on-disk raw-response cache (offline replay) |
| `store.py` | **the only** module that touches DuckDB |
| `season_data.py` | explicit sync/status/unmatched application service |
| `scoring.py` / `config.py` | pure league scoring (computed, never stored) |
| `identity.py` | canonical NFL team and DEF/DST identities (pure) |
| `names.py` | name normalization + `(name, pos)` crosswalk match (pure) |
| `rankings.py` | single-source ranked list |
| `consensus.py` | per-source points pivoted + averaged per player |
| `vorp.py` / `tiers.py` | replacement baselines + largest-gap tiers (pure) |
| `board.py` | consensus ⋈ ADP ⋈ byes + VORP + tiers → board rows + serializers (pure) |
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
records a single manual snake draft, and keeps the available board current. The
ordered available-player list is the primary recommendation and pick surface:
select a row, confirm it in Pick tools, and record the pick.

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

The separate `/mock` screen runs an isolated roster-aware rehearsal from an
immutable snapshot of the published board. Team count and rounds come from that
snapshot's `num_teams` and `roster_slots`; the Worker never substitutes tracker
defaults. Choose Brian's slot, a reproducible unsigned seed, and Calm, Realistic,
or Wild opponent variance. New mocks use the versioned `market-need-v1` strategy,
which combines market order, open starter needs, tier value, specialist timing,
and seeded variance. Existing `seeded-market-v0` mocks remain resumable.

At `1024px` and wider, the same `/mock` client becomes a two-pane workspace: the
board owns the left-side scroll area while selection, draft action, suggestions,
latest transition, and lifecycle controls remain in a bounded decision rail.
Below that breakpoint the existing single-column dock and its remembered **Pick
tools** disclosure remain unchanged. The route, `MockState`, `BoardViewState`,
saved board, API writes, and mock-only storage are shared across both layouts.

Mock mode uses the same Available/Drafted tabs, name search, position filters,
progressive list loading, row selection, Pick tools, identity-aware availability,
and snake clock as the live room. **Likely next** shows up to three available
players in market order (ADP, then ADP rank and board rank), restricted to
positions that keep Brian's configured roster completable. These suggestions are
advisory; the Worker independently enforces roster legality. Selecting a
suggestion is identical to selecting its board row.

Every user and CPU pick passes exact dedicated/flex/bench roster matching plus a
league-wide completion check, so an accepted pick cannot consume supply another
team needs to finish. Mock boards, configuration, teams, RNG state, and immutable
pick snapshots live in separate D1 tables and never mutate the live draft. The
same board snapshot, strategy version, preset, seed, and ordered user decisions
replay the same 160-pick Yahoo-shaped draft, including seed zero.

One Brian decision is one atomic request: the Worker records it, advances every
CPU turn through Brian's next decision or draft completion, and returns the full
authoritative state. The client then rebuilds availability, search, Drafted
history, tier counts, selection, clock, and suggestions from the returned picks
and the mock's saved immutable board—not from a newly published KV board.

An active mock can be paused and survives refresh with its seed, configuration,
picks, RNG state, and next turn unchanged; it must be explicitly resumed before
another pick. **Undo decision** rewinds Brian's latest choice plus every CPU pick
caused by that choice and restores the pre-decision RNG state. **Restart from
seed** keeps the mock ID, board snapshot, strategy, variance, and slot while
deterministically rebuilding the seeded opening. **Discard mock** removes the
isolated session and returns to setup. All mutations require the displayed
`mock_id` and monotonic `expected_revision`, so stale tabs cannot revive an old
state after undo or restart.

The authenticated lifecycle routes are `POST /api/mocks/current/pause`, `POST
/api/mocks/current/resume`, `DELETE /api/mocks/current/picks/latest`, `POST
/api/mocks/current/reset`, and `DELETE /api/mocks/current`. Their request body is
`{"mock_id":"…","expected_revision":N}`. Mock storage and API code use only
`mock_*` tables; lifecycle operations neither read nor mutate live draft picks.

The write API retains validated `manual_player` snapshots for compatibility when
a Yahoo pick is absent from the board, although the current client intentionally
offers only board-row selection. Before draft day, use a fresh local draft and
verify row selection, search replacement/restoration, record, undo, and reset in
live mode, then pause, resume, undo, restart, and discard a separate rehearsal in
mock mode.

```sh
cd tracker
nvm use                      # .nvmrc pins Node 22.23.1 / npm 10.9.8
npm ci
npm test                     # vitest + @cloudflare/vitest-pool-workers (offline)
npm run typecheck
npm run build:client         # verify the browser bundle
npm run test:browser         # build + real Chromium phone/desktop mock journey
npx wrangler d1 migrations apply ffb-tracker --local
npm run publish:board        # seed local KV from ../exports/board.json
npm run dev                  # wrangler dev (local KV + D1 via Miniflare)
```

Use the pinned tracker toolchain before any dependency or lockfile update.
`package.json` enforces the same Node/npm pair with `devEngines`, so a mismatched
package manager fails before it can rewrite `package-lock.json` into a shape CI
cannot consume.

For local `wrangler dev` you need a key: put `TRACKER_API_KEY=<anything>` in
`tracker/.dev.vars` (gitignored). Regenerate the board with `uv run ffb season
sync` followed by `uv run ffb board export` (writes `exports/board.json`), then
`npm run publish:board` to reload the dev store.

The browser suite serves committed fixtures on `127.0.0.1:4173`, uses the test
key internally, and covers `390×844`, `1024×768`, `1440×900`, and the
`1280×650` short-height fallback. It does not read or mutate local Wrangler D1
or KV state.

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
uv run ffb season sync 2026 --refresh  # when fresh source data is required
make deploy-board SEASON=2026          # export persisted data and publish production KV
make deploy-app      # typecheck/test, apply remote D1 migrations, deploy app/assets
make deploy-all      # deploy the app, then export/publish the persisted board
```

`deploy-board` never fetches source data; it exports the current DuckDB state
using the CLI's default draftable pool, checks that `exports/board.json` is
nonempty and holds at least `MIN_BOARD_PLAYERS` (100 by default) players, and
publishes that file. The count gate is what keeps a board hollowed out by a
degraded projection source from reaching production; `board export` itself
refuses to write an empty board, and both board commands print how many rankable
players the selected pool kept. Run the explicit `season sync` first for
projection, ADP, or schedule updates. Use `deploy-app` for Worker or browser-app
changes; it applies committed D1 migrations before deploying code that needs
them.

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
