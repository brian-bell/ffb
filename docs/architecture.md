# Architecture

`ffb` has two deployable parts joined by one file contract:

1. A Python data pipeline and CLI that owns ingestion, identity, scoring, and
   draft-board computation.
2. A TypeScript Cloudflare Worker that owns draft interaction and persistence.

The pipeline exports `board.json`; the tracker never imports or calls Python at
runtime.

## Python pipeline

### Write path

```text
ffb season sync
  → SeasonDataService
  → source fetch / SnapshotCache / pure parse
  → identity resolution
  → Store
  → DuckDB
```

`src/ffb/cli.py` renders commands and errors. `season_data.py` expands source
selectors, applies snapshot policy, attempts every requested source, and records
success or failure. `ingest.py` coordinates parsing, identity resolution, and
atomic slice replacement. Source modules contain only a thin fetch function and
a pure parser.

`store.py` is the only module that imports DuckDB. It owns all schema, writes,
queries, and transaction boundaries. The main stored domains are:

| Domain | Tables | Purpose |
| --- | --- | --- |
| Identity | `crosswalk`, `players` | Canonical and fallback player identities |
| Source data | `projections`, `adp`, `team_byes`, `injuries` | Normalized raw values used at read time |
| Source health | `season_source_state` | Attempts, successes, counts, snapshots, and errors |
| League context | `league_settings`, `league_teams`, `league_rosters` | Validated fixture-backed league state |

The DuckDB file is a disposable cache. Raw snapshots are the replay boundary;
cross-version schema migration is deliberately unsupported.

### Read and compute path

```text
DuckDB projections ──→ scoring ──→ consensus ──┐
DuckDB ADP ────────────────────────────────────┤
DuckDB team byes ──────────────────────────────┤
DuckDB injuries ───────────────────────────────┤
stored/fallback league context ────────────────┘
                                                ↓
                                  player-pool selection
                                                ↓
                                      VORP → tiers → ranks
                                                ↓
                              terminal / Markdown / CSV / JSON
```

Projection stat lines are stored, but fantasy points are not. `scoring.py`
applies the active league rules when data is read. `consensus.py` scores each
requested source independently and averages per player. `board.py` merges
consensus, ADP, and byes, selects the requested player pool, then derives VORP,
tiers, and ranks.

This ordering matters: the default draftable filter runs before all derived
values, so replacement baselines and ranks describe the board that the user
actually sees.

## Identity and eligibility

The nflverse `ff_playerids` dataset is the canonical spine. Normal player rows
use `mfl_id` as `player_key`; source ids resolve through the crosswalk. Team
defenses are absent from that dataset and use `def:<canonical-team>`.

Unresolved source rows remain stored under source-specific fallback keys and
are exposed through `season unmatched`. They are excluded from rankings and the
board until a later crosswalk refresh lets cached snapshots self-heal.

FFC ADP is the exception to id resolution because `ff_playerids` has no FFC id.
It matches by normalized name and position, drops free-agent candidates while
disambiguating, then uses canonical team as a tiebreak. Ambiguity is retained as
unmatched rather than guessed.

Draftability is distinct from identity. Projection parsers preserve each
provider's activity/team evidence. A consensus row is draftable when any
contributing projection source is affirmative; that projection evidence is
authoritative over ADP. A matched ADP-only row requires a current canonical FFC
team. Unknown evidence is negative.

## League context

`league.py` validates the closed provider-neutral `LeagueBundle` v1 before any
write. `league_context.py` loads synchronized scoring, roster slots, and team
count independently, falling back component by component to the confirmed
10-team Yahoo settings in `config.py`.

`sources/yahoo.py` implements the live `YahooLeagueSource` peer of
`FixtureLeagueSource` (httpx fetch, snapshot-cached raw pulls, pure mappers),
with the OAuth2 refresh-token lifecycle in `yahoo_auth.py`. The one-time
browser authorization (ffb-1ct.2) has not run yet, so live sync is inert until
a token exists; fixture import still exercises the provider boundary and
storage model without network access.

## Board contract

`board.json` version 1 is a self-contained envelope:

- `version`, `season`, `generated_at`, and scoring provenance;
- league `num_teams` and `roster_slots`;
- ordered players with identity, position/team/bye, points, source count, VORP,
  tier, board/position/ADP ranks, ADP range fields, and an optional canonical
  injury indicator carrying the source snapshot fetch time.

The optional injury field is additive within version 1: old exports and saved
mock boards without it remain valid. The board left-joins it by canonical
player key, so unmatched Sleeper records never receive an indicator.

The tracker validates the version and player shape. The Python package owns the
contract; any breaking envelope or player-field change requires a version bump
and a coordinated tracker update.

## Tracker boundary

The Worker streams the current board from KV key `board:current`. D1 stores
mutable state separately:

- `drafts`, `teams`, and `picks` hold the one live manual draft;
- `mock_boards`, `mock_drafts`, `mock_teams`, `mock_picks`, and
  `mock_checkpoints` hold isolated simulation state.

The client combines an immutable board with server-authoritative picks by
stable player identity. Republish updates recommendations and available board
data without rewriting pick history. See [tracker.md](tracker.md) for route,
state, and simulation details.

## Enforced boundaries

- `test_layering.py` keeps DuckDB access in `store.py` and compute modules free
  from I/O imports.
- Source and league replacement operations are atomic.
- The tracker pins `board.json` version 1 in code and fixtures.
- Live and mock draft stores are separate; mock routes do not read or mutate
  live draft tables.
- Python tests, tracker tests, and the cross-stack backend journey are all
  deterministic and network-free.
