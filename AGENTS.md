# AGENTS.md — ffb

Personal fantasy football pipeline for Brian's Yahoo league: pull free
projection sources, store raw + normalized data in DuckDB, and compute
league-scored, consensus-ranked output on a CLI. `DESIGN.md` holds the full
product design and the slice roadmap; this file is the working context for
agents changing the code.

## Build, test, run

Managed with **uv** (Python ≥ 3.12).

```sh
uv sync                      # install deps from uv.lock
uv run pytest                # test suite (offline, deterministic)
uv run ruff check .          # lint
uv run ruff format .         # format  (CI uses --check)
uv run ffb rankings --pos RB --sources   # run the CLI
```

CI (`.github/workflows/ci.yml`) runs `uv sync --frozen`, `ruff check`, `ruff
format --check`, and `pytest` on every push to `main` and every PR. It is
network-free: tests read `tests/fixtures/`, never the live APIs. If you change
dependencies, commit the updated `uv.lock` or `--frozen` will fail.

## Layout

```
src/ffb/
  cli.py            # Typer app; `ffb rankings` command + rich tables
  config.py         # paths, season, generic + league scoring weights, ESPN stat/position maps
  paths.py          # db/snapshot dirs, honoring FFB_DB_PATH / FFB_SNAPSHOT_DIR
  snapshot.py       # SnapshotCache: fetch-or-replay raw responses (offline)
  store.py          # THE ONLY module that imports duckdb
  scoring.py        # pure PPR scoring (no I/O)
  rankings.py       # single-source ranked list (pure compute over store)
  consensus.py      # per-source points pivoted + averaged per player (pure)
  ingest.py         # snapshot -> parse -> resolve to player_key -> store
  sources/
    sleeper.py      # Sleeper season projections: fetch + parse
    espn.py         # ESPN season projections: fetch + parse (numeric stat-id decode)
    crosswalk.py    # nflverse ff_playerids via nflreadpy -> identity spine
tests/              # pytest; fixtures/ are trimmed real API responses
docs/plans/         # per-slice implementation plans (gitignored, local-only)
snapshots/          # raw API pulls, offline replay cache (gitignored)
data/ffb.duckdb     # the store (gitignored, rebuilt from snapshots)
```

## Key modules & responsibilities

- **store.py** — the single DuckDB gateway. Tables: `crosswalk` (identity spine),
  `players` (canonical/​fallback identity + `matched` flag), `projections` (keyed
  by `(player_key, season, source, scope)` — the `source`/`scope` columns keep
  weekly projections additive later — carrying `native_id`, `stats_json`,
  `src_pts_ppr`). Exposes writes (`upsert_crosswalk`, `replace_crosswalk`,
  `upsert_projections`, `delete_projections`), the resolver (`resolve`,
  `resolve_batch`), and reads (`projection_rows`, `has_season`, `has_crosswalk`,
  `has_stale_resolution`).
- **ingest.py** — `ensure_crosswalk` loads the spine; `ensure_ingested` (Sleeper)
  and `ensure_espn_ingested` load a source, resolving each native id to a
  canonical `player_key`. Each re-ingest replaces the source's `(season, source)`
  slice (via `delete_projections`) so a refresh mirrors the source instead of
  unioning with stale rows. Returns a `Reconciliation` (matched/unmatched counts +
  sample names). Idempotent and offline via the snapshot cache.
- **consensus.py** — groups the requested sources by `player_key`, scores each
  source's stat line, and averages the points; carries `n` (source count). The
  `sources` argument restricts which sources contribute, so output depends on the
  request rather than on whatever a prior run happened to persist.
- **sources/** — each source is a thin `fetch` + pure `parse` pair with a
  `snapshot_key`. No plugin abstraction yet; two concrete sources.

## Conventions & invariants

- **Only `store.py` imports `duckdb`.** Enforced by
  `test_only_store_module_imports_duckdb`. Route all DB access through the store.
- **Points are computed, never stored.** `scoring.py` scores `stats_json` at read
  time, so re-scoring to Yahoo league settings (slice 4) is a config swap, not a
  re-ingest. `src_pts_ppr` is only the source's own total, kept for validation.
- **Canonical identity via the crosswalk.** Every source's native id resolves to
  a `player_key` (nflverse `mfl_id`) so consensus aligns players across sources.
  Matched players take canonical crosswalk identity (name/pos/team) so one source
  can't clobber another's. A crosswalk **miss is never dropped** — it falls back
  to a `source:native_id` key with `matched=False` and is surfaced in the
  `Reconciliation`, the WARNING logs, and the CLI footer.
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
  failed crosswalk load leaves players unmatched (not a crash), and a failed ESPN
  fetch falls back to Sleeper-only. Both print a yellow notice; only a missing
  Sleeper snapshot while offline is fatal.
- **Offline by default.** Every raw pull is snapshotted under `snapshots/` and
  replayed; `--refresh` forces a network re-fetch. Tests/CI use `tests/fixtures/`
  (committed), not `snapshots/` (gitignored).
- **TDD.** Red → green per behavior; fixtures are small, hand-trimmed real
  responses. Keep tests behavior-level (public interfaces), not implementation.
- **Layering:** `cli → {consensus, rankings, ingest} → {store, scoring}`;
  `sources → snapshot`. `consensus.py`/`rankings.py` are pure (store rows in,
  dict rows out).
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
- **K/DST are scored but effectively single-source.** `LEAGUE_SCORING` includes
  kicking and defense weights (keyed to Sleeper's stat line), so K/DEF rank on
  real points, not zero. But cross-source consensus for them is still limited:
  ESPN's K/DST stat ids aren't in `ESPN_STAT_MAP`, so `espn.parse_projections`
  drops any row with no scorable stats (self-healing once those ids are mapped —
  a separate spike) rather than emit 0-point rows that would halve a
  crosswalk-matched kicker's consensus; and team defenses aren't in
  `ff_playerids` and Sleeper labels them `DEF` vs ESPN's `DST`, so defenses don't
  form a consensus. Expected; see DESIGN "Open items".
- **Schema or parse-logic changes need a fresh DB.** The store uses `CREATE TABLE
  IF NOT EXISTS` and `ensure_*` skip re-processing when a slice is already
  present, so neither a column change nor a parse/normalization change that alters
  derived-row *content* (e.g. crosswalk `PK`→`K`, or a source dropping unscorable
  rows) reaches an existing `data/ffb.duckdb`. Delete it and re-ingest (offline,
  from snapshots) after such a change. The DB is a disposable cache, so this is
  cheap.
- **Yahoo isn't wired yet.** The crosswalk carries `yahoo_id` for later, but no
  Yahoo projections are ingested (tasks 2/9).
```
