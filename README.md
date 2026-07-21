# ffb — fantasy football pipeline

Personal tooling for Brian's Yahoo league. See [`DESIGN.md`](DESIGN.md) for the
full design.

## Status: Slice 1 — walking skeleton

Proves the **ingest → store → compute → display** spine end to end with one real
data source (Sleeper season projections):

```
uv run ffb rankings --pos RB
```

```
                   2024 rankings — RB
┏━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━┳━━━━━━━━━━━━┓
┃ Rank ┃ Player              ┃ Pos ┃ Team ┃ Proj (PPR) ┃
┡━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━╇━━━━━━━━━━━━┩
│    1 │ Derrick Henry       │ RB  │ BAL  │      288.0 │
│    2 │ Breece Hall         │ RB  │ NYJ  │      284.2 │
│  ... │                     │     │      │            │
└──────┴─────────────────────┴─────┴──────┴────────────┘
```

## Setup

```sh
uv sync
```

## Usage

```sh
uv run ffb rankings                    # all positions, default season
uv run ffb rankings --pos RB           # filter position
uv run ffb rankings --pos WR --limit 40
uv run ffb rankings --season 2024      # pick season
uv run ffb rankings --pos RB --refresh # re-fetch live from Sleeper
```

By default runs are **offline**: the raw Sleeper response is snapshotted under
`snapshots/` on first fetch and replayed on later runs. `--refresh` forces a new
network pull and overwrites the snapshot.

## How it fits together

| Module | Role |
|---|---|
| `sources/sleeper.py` | fetch + parse Sleeper projections |
| `snapshot.py` | on-disk raw-response cache (offline replay) |
| `store.py` | **the only** module that touches DuckDB |
| `scoring.py` / `config.py` | pure PPR scoring (computed, never stored) |
| `rankings.py` | join stored projections with scoring → ranked list |
| `ingest.py` | snapshot → parse → store wiring (idempotent) |
| `cli.py` | `ffb` command, rich table output |

Points are **computed** from stat lines at read time, not stored — so slice 4
can re-score to exact Yahoo league settings by swapping the scoring config.

## Development

```sh
uv run pytest              # test suite
uv run ruff check .        # lint
uv run ruff format .       # format
```

Test-driven; CI runs lint + format + tests on every push and PR.
