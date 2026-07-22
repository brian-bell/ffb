# AGENTS.md — ffb

Personal fantasy football pipeline for Brian's Yahoo league: pull free
projection + ADP sources, store raw + normalized data in DuckDB, and compute
league-scored, consensus-ranked output on a CLI — rankings plus a draft cheat
sheet (VORP, tiers, ADP) with a self-contained `board.json` export. `DESIGN.md`
holds the full product design and the slice roadmap; this file is the working
context for agents changing the code.

## Build, test, run

The root Makefile installs both toolchains and exposes the production deploy
workflows:

```sh
make init                     # uv sync + npm install in tracker/
make test-backend-e2e         # offline fixture -> CLI -> DuckDB/board -> Worker KV/D1 journey
make deploy-board             # refresh/export board.json, then publish production KV
make deploy-app               # tracker checks + remote D1 migrations + Worker deploy
make deploy-all               # app first, then board
```

The Python package is managed with **uv** (Python ≥ 3.12).

```sh
uv sync                      # install deps from uv.lock
uv run pytest                # test suite (offline, deterministic)
uv run ruff check .          # lint
uv run ruff format .         # format  (CI uses --check)
uv run ffb rankings --pos RB --sources   # consensus rankings
uv run ffb cheatsheet --export           # draft board + board.json export
```

The tracker has its own Node toolchain:

```sh
cd tracker
npm ci
npm run typecheck
npm test
npm run build:client
```

Before committing a change that can affect fixture ingestion, CLI board export,
the `board.json` contract, D1 migrations, KV/D1 behavior, or Worker APIs, run
`make test-backend-e2e` from the repository root. It exercises the complete
backend boundary using committed fixtures and isolated temporary storage, with
no live network or Cloudflare dependencies. Set `FFB_E2E_KEEP_TMP=1` to retain
the temporary database, snapshots, and export when diagnosing a failure.

`deploy-board` is intentionally a data-only deployment: it runs `ffb
cheatsheet --refresh --export`, verifies `exports/board.json` is nonempty, and
writes `board:current` to production KV without redeploying code. `deploy-app`
runs the tracker typecheck and tests, applies pending remote D1 migrations, then
builds and deploys the Worker and static assets. Both require an authenticated
Wrangler session; first-time secret setup and key rotation remain manual.

CI (`.github/workflows/ci.yml`) runs three independent jobs on every push to
`main` and every PR. The Python job runs `uv sync --frozen`, `ruff check`, `ruff
format --check`, and `pytest`; the tracker job runs `npm ci`, `typecheck`, and
Vitest; the backend E2E job installs both toolchains and runs
`make test-backend-e2e`. All three suites are network-free. If Python
dependencies change, commit the updated `uv.lock` or `--frozen` will fail; if
tracker dependencies change, commit `tracker/package-lock.json` too.

## Layout

```
Makefile            # install, board-publish, and tracker-deploy workflows
src/ffb/
  cli.py            # Typer app; `ffb rankings` + `ffb cheatsheet` commands + rich tables
  config.py         # paths, season, scoring weights, ESPN maps, league shape, FFC/VORP/tier knobs
  paths.py          # db/snapshot/export dirs, honoring FFB_DB_PATH / FFB_SNAPSHOT_DIR / FFB_EXPORT_DIR
  snapshot.py       # SnapshotCache: fetch-or-replay raw responses (offline)
  store.py          # THE ONLY module that imports duckdb
  scoring.py        # pure PPR scoring (no I/O)
  names.py          # normalize_name + (name, pos) crosswalk match, team tiebreak (pure)
  rankings.py       # single-source ranked list (pure compute over store)
  consensus.py      # per-source points pivoted + averaged per player (pure)
  vorp.py           # replacement baselines via greedy starter fill + VORP (pure)
  tiers.py          # largest-gap positional tiers within a draftable pool (pure)
  board.py          # consensus ⋈ adp + vorp + tiers -> board rows + md/csv/json serializers (pure)
  league.py         # provider-neutral LeagueBundle v1 + strict fixture adapter/validation
  league_context.py # stored rules -> safe scoring/roster/team-count runtime context
  ingest.py         # snapshot -> parse -> resolve to player_key -> store
  sources/
    sleeper.py      # Sleeper season projections: fetch + parse
    espn.py         # ESPN season projections: fetch + parse (numeric stat-id decode)
    ffc.py          # Fantasy Football Calculator ADP: fetch + parse (PK->K, team alias)
    crosswalk.py    # nflverse ff_playerids via nflreadpy -> identity spine
tests/              # pytest; fixtures/ are trimmed real API responses
  e2e/              # snapshot primer + isolated CLI-to-Worker orchestration
docs/plans/         # per-slice implementation plans (gitignored, local-only)
snapshots/          # raw API pulls, offline replay cache (gitignored)
exports/            # `ffb cheatsheet --export` output (board.json etc.; gitignored)
data/ffb.duckdb     # the store (gitignored, rebuilt from snapshots)
tracker/            # SEPARATE TS Cloudflare Worker subtree (own npm/vitest) — draft
                    #   tracker consuming board.json v1; not part of the uv package
```

## Draft tracker (`tracker/`) — separate TS subtree

`tracker/` is a **TypeScript Cloudflare Worker**, isolated from the Python
package (own `package.json`, `wrangler.jsonc`, `vitest`; uv stays Python-only).
It's the slice-8 draft tracker: a thin render/auth layer that consumes the
pipeline's `board.json` **v1** contract as its input API — the only
cross-boundary coupling is `tracker/` reading `../exports/board.json` at publish
time (a file path, **not** a Python import). Nothing in `src/ffb/` knows about it.

- **Board data path:** the whole board blob lives in a **KV** namespace (`BOARD`,
  key `board:current`); the Worker serves it verbatim from an auth-gated `GET
  /api/board` (streams KV text straight through — the pipeline owns the shape).
  Local re-publishing = `npm run publish:board`; production re-publishing =
  `make deploy-board` from the repository root. Both are KV writes with **no
  code redeploy**.
- **Auth:** single shared bearer key (`TRACKER_API_KEY`, a Wrangler secret,
  constant-time compared) gates `/api/*`; the static shell is public so the phone
  can load and enter the key (saved in `localStorage`, in-memory fallback).
- **Contract pin:** the client asserts `board.version === 1` and degrades loudly
  ("redeploy the tracker") on drift — this is what `board.py`'s `version` field
  is for. The committed `tracker/test/fixtures/board.json` keeps tracker tests
  green independent of the live pipeline.
- **D1 draft state:** migration `0002_draft_state.sql` stores one configurable
  current draft, ordered teams, and immutable pick snapshots. `src/draft-store.ts`
  is the only D1 query gateway; `draft-api.ts` is the `/api/*` request router (the
  `GET`/`PUT /api/draft`, `POST /api/picks`, `DELETE /api/picks/latest`, `DELETE
  /api/draft` handlers). The Worker derives snake order rather than persisting
  it, validates stale expected-pick writes, supports latest-only undo, accepts a
  validated `manual_player` snapshot when a Yahoo pick is absent from the board,
  and resets by explicitly deleting picks, teams, then draft (never relying on
  FK cascade).
- **Recommendation + availability:** `recommendation.ts` derives Brian-only,
  explainable recommendations from the immutable board plus live D1 picks. It
  fills dedicated starters before flex, considers tier survival and VORP cliffs,
  delays K/DEF until forced, and shows no Brian-specific recommendation on an
  opponent turn. `player-identity.ts` centralizes canonical/fallback/manual and
  DEF/DST equivalence so suggestions, search, recommendations, and the write API
  exclude the same drafted player representations.
- **Pure testable core:** `src/{auth,board,board-view,draft,player-identity,
  recommendation,recommendation-view,suggestions,render,setup,state}.ts` are
  pure/DOM-free and unit-tested; `types.ts` mirrors the `board.json` v1 contract;
  `index.ts` is the Worker entry (auth-gate + KV board stream + fall through to
  Static Assets), and `draft-api.ts` the API router. `public/app.ts` is thin DOM
  wiring bundled to `public/app.js` (esbuild, gitignored).
- CI: a **separate** `tracker` job (Node) runs `typecheck` + `vitest`, independent
  of the Python `uv` job; a third `backend-e2e` job generates a real board with
  the Python CLI and drives it through the Worker with Miniflare. The provisioned
  KV/D1 ids and `ffb.bbell.dev` route in `wrangler.jsonc` are non-secret deployment
  configuration; `TRACKER_API_KEY` remains a Wrangler secret (see README).

## Key modules & responsibilities

- **store.py** — the single DuckDB gateway. Tables: `crosswalk` (identity spine),
  `players` (canonical/​fallback identity + `matched` flag), `projections` (keyed
  by `(player_key, season, source, scope)` — the `source`/`scope` columns keep
  weekly projections additive later — carrying `native_id`, `stats_json`,
  `src_pts_ppr`). Exposes writes (`upsert_crosswalk`, `replace_crosswalk`,
  `upsert_projections`, `delete_projections`), the resolver (`resolve`,
  `resolve_batch`), and reads (`projection_rows`, `has_season`, `has_crosswalk`,
  `has_stale_resolution`). Plus the `adp` table (keyed by `(player_key, season,
  source)`, carrying FFC's own name/pos/team + ADP fields) with
  `upsert_adp`/`delete_adp`/`adp_rows`, and `crosswalk_rows` (a spine read the
  name matcher consumes, keeping `duckdb` confined here).
- **ingest.py** — `ensure_crosswalk` loads the spine; `ensure_ingested` (Sleeper)
  and `ensure_espn_ingested` load a source, resolving each native id to a
  canonical `player_key`. Each re-ingest replaces the source's `(season, source)`
  slice (via `delete_projections`) so a refresh mirrors the source instead of
  unioning with stale rows. Returns a `Reconciliation` (matched/unmatched counts +
  sample names). Idempotent and offline via the snapshot cache. `ensure_adp_ingested`
  is the FFC entry point: it name-resolves (no id column) and re-parses/re-resolves
  from the cached snapshot on **every** run (§ ADP self-heal below).
- **league.py / league_context.py** — `FixtureLeagueSource` validates the closed
  provider-neutral `LeagueBundle` v1 before any write; `load_league_context`
  independently substitutes complete mock scoring/roster settings, while always
  using a valid stored team count. Live Yahoo/YFPY remains Task 2b.
- **consensus.py** — groups the requested sources by `player_key`, scores each
  source's stat line, and averages the points; carries `n` (source count). The
  `sources` argument restricts which sources contribute, so output depends on the
  request rather than on whatever a prior run happened to persist.
- **names.py / vorp.py / tiers.py / board.py** — the cheat-sheet compute stack, all
  pure (dicts in, dicts/strings out; enforced by `test_layering`). `names` does
  `(normalized name, position)` matching with an FA-drop + team tiebreak; `vorp`
  derives per-position replacement baselines by greedily filling every league
  starting slot (flex-aware) and subtracts them; `tiers` splits each position at its
  largest point drops; `board` left-joins ADP onto consensus, appends ADP-only rows,
  computes VORP + tiers, ranks, and serializes to the `board.json` contract / md / csv.
- **sources/** — each source is a thin `fetch` + pure `parse` pair with a
  `snapshot_key`. No plugin abstraction yet; three projection/ADP sources.

## Conventions & invariants

- **Only `store.py` imports `duckdb`.** Enforced by
  `test_only_store_module_imports_duckdb`. Route all DB access through the store.
- **Points are computed, never stored.** `scoring.py` scores `stats_json` at read
  time, so re-scoring to Yahoo league settings (slice 4) is a config swap, not a
  re-ingest. `src_pts_ppr` is only the source's own total, kept for validation.
  **VORP and tiers are computed too** — derived in `board.py` at read time, never
  stored, so a scoring or roster-shape change re-derives the whole board with no
  re-ingest. **ADP is a *source* value, so it *is* stored** (the `adp` table);
  storing it doesn't violate this rule.
- **ADP resolves by name, and self-heals every run.** FFC's `player_id` has no
  column in `ff_playerids`, so `ensure_adp_ingested` resolves each row by
  `(normalized name, position)` with a team tiebreak (`names.py`); ambiguity →
  *unmatched*, never a guess (a wrong merge silently corrupts the board). There's
  no id-based staleness detector for name-resolved rows, so ADP just re-parses +
  re-resolves from the cached snapshot on every run (delete-then-insert mirror) —
  which buys the late-crosswalk self-heal for free. Network is still only touched
  on `--refresh` or the first pull.
- **board.json is a versioned, self-contained contract.** `board.py` emits it with
  a `version` (bumped on any breaking shape change — the slice-6 tracker pins
  against it) and everything the tracker needs (tiers, ADP, positions, names). The
  CLI does the file writing; `board.py` stays I/O-free and takes `generated_at` as
  an argument so it's deterministic under test.
- **Canonical identity via the crosswalk (plus team-defense keys).** Player
  sources resolve to an nflverse `mfl_id`; team defenses, which are absent from
  `ff_playerids`, resolve to `def:<canonical MFL team code>`. Valid canonical
  identities carry `matched=True`. Anything unresolved is never dropped: it
  falls back to `source:native_id` with `matched=False` and is surfaced in the
  `Reconciliation`, WARNING logs, and CLI footer.
- **Late crosswalk self-heals.** A source ingested before the crosswalk was
  available is stranded under fallback keys. On the next run, `has_stale_resolution`
  detects rows that now map to a different (canonical) key and forces an offline
  re-ingest from the cached snapshot — no `--refresh` needed.
- **Crosswalk refresh mirrors the source.** `ensure_crosswalk` uses
  `replace_crosswalk` (clear + reinsert in one transaction), so a `--refresh`
  that drops or reassigns a native id upstream can't leave a stale row for
  `resolve` to match. An empty parse is treated as a bad pull and leaves the
  existing spine untouched.
- **Best-effort degradation.** The CLI ranks even when pieces are missing: a
  failed crosswalk load leaves players unmatched (not a crash), a failed ESPN
  fetch falls back to Sleeper-only, and a failed FFC pull drops the ADP columns.
  Each prints a yellow notice; only a missing Sleeper snapshot while offline is
  fatal. `rankings` and `cheatsheet` share the ingest block via `_ingest_sources`.
- **Offline by default.** Every raw pull is snapshotted under `snapshots/` and
  replayed; `--refresh` forces a network re-fetch. Tests/CI use `tests/fixtures/`
  (committed), not `snapshots/` (gitignored).
- **TDD.** Red → green per behavior; fixtures are small, hand-trimmed real
  responses. Keep tests behavior-level (public interfaces), not implementation.
- **Layering:** `cli → {consensus, rankings, board, ingest} → {store, scoring}`;
  `board → {vorp, tiers}`; `sources → snapshot`; `names` is pure, imported by
  ingest only. `consensus`/`rankings`/`vorp`/`tiers`/`board`/`names` are pure
  (data in, data out) — `test_layering` guards them against I/O imports.
- **Git:** never commit to `main`; branch per slice (e.g.
  `slice-03-crosswalk-espn-consensus`). `docs/plans/`, `snapshots/`, and `data/`
  are gitignored.

## Gotchas

- **nflverse tooling is `nflreadpy`, not `nfl_data_py`.** `nfl_data_py` pins
  `pandas==1.5.3`, which does not build on Python 3.12. `nflreadpy` (polars) is
  the maintained successor.
- **nflreadpy ids are polars `Int64`.** `crosswalk.parse_crosswalk` stringifies
  every id and null-guards, so joins are string-to-string.
- **ESPN is an unofficial endpoint.** The `/players` response is a *bare
  top-level JSON list*; the season projection is the `stats[]` entry with
  `statSourceId==1 && scoringPeriodId==0`; stats are `{numeric statId: value}`
  decoded via `config.ESPN_STAT_MAP`. `appliedTotal` is 0 in this view, so ESPN
  rows carry `src_pts_ppr=None` and we score them ourselves. If ESPN drifts, the
  committed fixture keeps CI green; re-verify against a fresh `--refresh` pull.
- **K/DEF form a Sleeper + ESPN consensus.** ESPN's numeric kicking and defense
  stat ids decode into the same keys `LEAGUE_SCORING` uses for Sleeper. The ESPN
  18–21 points-allowed bucket maps to Yahoo's 14–20 bucket as an explicit
  approximation, and source buckets that collapse onto one league category are
  added. ESPN `DST` normalizes to `DEF`; Sleeper, ESPN, and FFC defense rows share
  `def:<canonical team>` identity. Rows with no decoded scorable stats are still
  dropped rather than emitted at zero, which avoids diluting consensus.
- **FFC ADP is free but informal** (no auth, no SLA, undocumented). The response
  is `{status, meta, players:[…]}`; `parse_adp` returns `[]` on a non-`Success`
  status (so the snapshot `is_valid` gate treats a bad refresh as empty) and
  normalizes `PK→K` + aliases team codes (`SF→SFO`, via `config.TEAM_ALIASES`)
  so the name matcher's team tiebreak compares like with like. We pull one
  format/teams combo (`config.FFC_FORMAT`/`LEAGUE_NUM_TEAMS`); a 10-team or
  half-PPR league shifts that (and the VORP baselines) — both config reads.
- **Team defenses share synthetic canonical identity.** DEF isn't in
  `ff_playerids`, so valid team-defense rows bypass player-name resolution and
  use `def:<canonical MFL team code>` across Sleeper, ESPN, and FFC. Unknown team
  codes remain source fallbacks rather than risking a wrong merge. The remaining
  unmatched ADP tail is typically nickname/ambiguous misses like "Hollywood
  Brown" or two "Mike Williams"; extending `normalize_name`/`TEAM_ALIASES` is the
  tuning loop. A player whose position doesn't map (`None`) is still boarded
  (never dropped) under an "Unknown" section.
- **Schema or parse-logic changes need a fresh DB.** The store uses `CREATE TABLE
  IF NOT EXISTS` and `ensure_*` skip re-processing when a slice is already
  present, so neither a column change nor a parse/normalization change that alters
  derived-row *content* (e.g. crosswalk `PK`→`K`, or a source dropping unscorable
  rows) reaches an existing `data/ffb.duckdb`. Delete it and re-ingest (offline,
  from snapshots) after such a change. The DB is a disposable cache, so this is
  cheap.
- **Yahoo is fixture-backed only.** `ffb league sync --fixture PATH` stores mock
  league settings, teams, and current-week rosters atomically; it has no OAuth or
  live requests. Task 2b adds YFPY behind the existing provider boundary. Yahoo
  projections remain future work (Task 9).
