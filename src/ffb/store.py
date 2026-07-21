"""DuckDB store — the ONLY module that touches the database.

Everything else (ingest, rankings, CLI) goes through this class. A test
(``test_only_store_module_imports_duckdb``) enforces that contract.

Schema is deliberately small for the walking skeleton but shaped for growth:
``source`` and ``scope`` columns exist now so consensus (slice 3) and weekly
projections (slice 9) are additive, not migrations. Scoring is NOT stored —
points are computed from ``stats_json`` at read time so rankings stay
reproducible and league re-scoring (slice 4) is a config swap.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id VARCHAR PRIMARY KEY,
    full_name VARCHAR,
    position  VARCHAR,
    team      VARCHAR
);

CREATE TABLE IF NOT EXISTS projections (
    player_id   VARCHAR,
    season      INTEGER,
    source      VARCHAR,
    scope       VARCHAR,
    stats_json  VARCHAR,
    src_pts_ppr DOUBLE,
    PRIMARY KEY (player_id, season, source, scope)
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))

    def init_schema(self) -> None:
        self.conn.execute(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- writes -----------------------------------------------------------
    def upsert_projections(self, rows: list[dict[str, Any]]) -> None:
        """Insert-or-replace player metadata and their projections."""
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO players (player_id, full_name, position, team)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (player_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    position  = excluded.position,
                    team      = excluded.team
                """,
                [row["player_id"], row["full_name"], row["position"], row["team"]],
            )
            self.conn.execute(
                """
                INSERT INTO projections
                    (player_id, season, source, scope, stats_json, src_pts_ppr)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (player_id, season, source, scope) DO UPDATE SET
                    stats_json  = excluded.stats_json,
                    src_pts_ppr = excluded.src_pts_ppr
                """,
                [
                    row["player_id"],
                    row["season"],
                    row["source"],
                    row["scope"],
                    json.dumps(row["stats"]),
                    row["src_pts_ppr"],
                ],
            )

    def delete_projections(
        self, season: int, source: str = "sleeper", scope: str = "season"
    ) -> None:
        """Remove a (season, source, scope) slice so a re-ingest mirrors the
        source rather than unioning with stale rows."""
        self.conn.execute(
            "DELETE FROM projections WHERE season = ? AND source = ? AND scope = ?",
            [season, source, scope],
        )

    # --- reads ------------------------------------------------------------
    def has_season(self, season: int) -> bool:
        result = self.conn.execute(
            "SELECT COUNT(*) FROM projections WHERE season = ?", [season]
        ).fetchone()
        return bool(result and result[0] > 0)

    def projection_rows(
        self,
        season: int,
        position: str | None = None,
        source: str = "sleeper",
        scope: str = "season",
    ) -> list[dict[str, Any]]:
        """Return joined player + projection rows (stats decoded to dicts)."""
        query = """
            SELECT p.player_id, pl.full_name, pl.position, pl.team,
                   p.season, p.source, p.stats_json, p.src_pts_ppr
            FROM projections p
            JOIN players pl ON pl.player_id = p.player_id
            WHERE p.season = ? AND p.source = ? AND p.scope = ?
        """
        params: list[Any] = [season, source, scope]
        if position is not None:
            query += " AND UPPER(pl.position) = UPPER(?)"
            params.append(position)

        cursor = self.conn.execute(query, params)
        cols = [c[0] for c in cursor.description]
        out: list[dict[str, Any]] = []
        for values in cursor.fetchall():
            record = dict(zip(cols, values, strict=True))
            record["stats"] = json.loads(record.pop("stats_json"))
            out.append(record)
        return out
