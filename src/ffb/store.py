"""DuckDB store — the ONLY module that touches the database.

Everything else (ingest, rankings, CLI) goes through this class. A test
(``test_only_store_module_imports_duckdb``) enforces that contract.

Projections are keyed by a canonical ``player_key`` (nflverse ``mfl_id``) so
multiple sources align for consensus; the ``crosswalk`` table maps each source's
native id onto it, and ``source``/``scope`` columns keep weekly projections
(slice 9) additive. Scoring is NOT stored — points are computed from
``stats_json`` at read time so rankings stay reproducible and league re-scoring
(slice 4) is a config swap.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS crosswalk (
    player_key VARCHAR PRIMARY KEY,   -- canonical id (nflverse mfl_id)
    sleeper_id VARCHAR,
    espn_id    VARCHAR,
    yahoo_id   VARCHAR,               -- carried for Yahoo ingest (tasks 2/9)
    gsis_id    VARCHAR,
    full_name  VARCHAR,
    position   VARCHAR,
    team       VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_xwalk_sleeper ON crosswalk (sleeper_id);
CREATE INDEX IF NOT EXISTS ix_xwalk_espn    ON crosswalk (espn_id);

CREATE TABLE IF NOT EXISTS players (
    player_key VARCHAR PRIMARY KEY,   -- canonical, or 'source:native_id' fallback
    full_name  VARCHAR,
    position   VARCHAR,
    team       VARCHAR,
    matched    BOOLEAN                 -- false when identity is a crosswalk miss
);

CREATE TABLE IF NOT EXISTS projections (
    player_key  VARCHAR,
    season      INTEGER,
    source      VARCHAR,
    scope       VARCHAR,
    native_id   VARCHAR,               -- the source's own id (provenance)
    stats_json  VARCHAR,
    src_pts_ppr DOUBLE,
    PRIMARY KEY (player_key, season, source, scope)
);

CREATE TABLE IF NOT EXISTS adp (
    player_key    VARCHAR,             -- canonical, or 'ffc:<player_id>' fallback
    season        INTEGER,
    source        VARCHAR,             -- 'ffc' (format/teams live in config)
    native_id     VARCHAR,             -- FFC player_id (provenance)
    full_name     VARCHAR,             -- FFC's own name (display for unmatched rows)
    position      VARCHAR,             -- normalized (PK->K)
    team          VARCHAR,             -- aliased to nflverse style at parse time
    bye           INTEGER,
    adp           DOUBLE,
    adp_high      INTEGER,
    adp_low       INTEGER,
    adp_stdev     DOUBLE,
    times_drafted INTEGER,
    matched       BOOLEAN,
    PRIMARY KEY (player_key, season, source)
);

CREATE TABLE IF NOT EXISTS league_settings (
    season INTEGER PRIMARY KEY,
    league_id VARCHAR, league_key VARCHAR, name VARCHAR, current_week INTEGER,
    num_teams INTEGER, source VARCHAR, synced_at VARCHAR,
    roster_slots_json VARCHAR, scoring_rules_json VARCHAR,
    unmapped_scoring_rules_json VARCHAR, provider_settings_json VARCHAR
);
CREATE TABLE IF NOT EXISTS league_teams (
    season INTEGER, team_key VARCHAR, team_id VARCHAR, name VARCHAR,
    managers_json VARCHAR, is_user_team BOOLEAN,
    PRIMARY KEY (season, team_key)
);
CREATE TABLE IF NOT EXISTS league_rosters (
    season INTEGER, week INTEGER, team_key VARCHAR, yahoo_player_id VARCHAR,
    yahoo_player_key VARCHAR, full_name VARCHAR, nfl_team VARCHAR,
    primary_position VARCHAR, eligible_positions_json VARCHAR, selected_position VARCHAR,
    player_key VARCHAR, matched BOOLEAN,
    PRIMARY KEY (season, week, team_key, yahoo_player_id)
);
"""

# Columns of the adp table, in insert order (also the read column order).
_ADP_COLUMNS = (
    "player_key",
    "season",
    "source",
    "native_id",
    "full_name",
    "position",
    "team",
    "bye",
    "adp",
    "adp_high",
    "adp_low",
    "adp_stdev",
    "times_drafted",
    "matched",
)

# Which crosswalk column holds each source's native player id.
_SOURCE_ID_COLUMN = {"sleeper": "sleeper_id", "espn": "espn_id", "yahoo": "yahoo_id"}


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))

    def init_schema(self) -> None:
        # No cross-version migration by design: the DB is a disposable local
        # cache rebuilt offline from snapshots (see AGENTS.md). Delete the file
        # after a schema change rather than migrating it.
        self.conn.execute(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- writes -----------------------------------------------------------
    def upsert_crosswalk(self, rows: list[dict[str, Any]]) -> None:
        """Insert-or-replace crosswalk spine rows (the nflverse identity map)."""
        self._insert_crosswalk_rows(rows)

    def replace_crosswalk(self, rows: list[dict[str, Any]]) -> None:
        """Mirror the crosswalk source: atomically clear the table, then insert.

        A refresh must not union with the previous snapshot. Upserting alone is
        keyed on ``player_key``, so a native id (``sleeper_id``/``espn_id``)
        removed or reassigned upstream would leave its old row behind and
        ``resolve``/``resolve_batch`` could still match — even duplicate-match
        nondeterministically — the wrong player. DELETE + insert in one
        transaction so the table always reflects exactly the fresh pull.
        """
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute("DELETE FROM crosswalk")
            self._insert_crosswalk_rows(rows)
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _insert_crosswalk_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO crosswalk
                    (player_key, sleeper_id, espn_id, yahoo_id, gsis_id,
                     full_name, position, team)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (player_key) DO UPDATE SET
                    sleeper_id = excluded.sleeper_id,
                    espn_id    = excluded.espn_id,
                    yahoo_id   = excluded.yahoo_id,
                    gsis_id    = excluded.gsis_id,
                    full_name  = excluded.full_name,
                    position   = excluded.position,
                    team       = excluded.team
                """,
                [
                    row["player_key"],
                    row.get("sleeper_id"),
                    row.get("espn_id"),
                    row.get("yahoo_id"),
                    row.get("gsis_id"),
                    row.get("full_name"),
                    row.get("position"),
                    row.get("team"),
                ],
            )

    def _source_column(self, source: str) -> str:
        column = _SOURCE_ID_COLUMN.get(source)
        if column is None:
            raise ValueError(f"no crosswalk column for source {source!r}")
        return column

    def resolve(self, source: str, native_id: str) -> str | None:
        """Map a source's native player id to the canonical ``player_key``.

        Returns ``None`` on a crosswalk miss (caller decides the fallback).
        """
        column = self._source_column(source)
        result = self.conn.execute(
            f"SELECT player_key FROM crosswalk WHERE {column} = ? LIMIT 1",
            [native_id],
        ).fetchone()
        return result[0] if result else None

    def resolve_batch(self, source: str, native_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Resolve many native ids at once to ``{native_id: crosswalk row}``.

        Returns canonical ``player_key`` + identity for the matches only; misses
        are simply absent. One query so ingest doesn't fan out per player.
        """
        column = self._source_column(source)
        ids = [i for i in native_ids if i is not None]
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        cursor = self.conn.execute(
            f"""
            SELECT {column} AS native_id, player_key, full_name, position, team
            FROM crosswalk
            WHERE {column} IN ({placeholders})
            """,
            ids,
        )
        cols = [c[0] for c in cursor.description]
        out: dict[str, dict[str, Any]] = {}
        for values in cursor.fetchall():
            row = dict(zip(cols, values, strict=True))
            out[str(row["native_id"])] = row
        return out

    def upsert_projections(self, rows: list[dict[str, Any]]) -> None:
        """Insert-or-replace player identity and their projections.

        Rows are keyed by canonical ``player_key`` (resolved in ingest); each
        carries its source ``native_id`` for provenance and a ``matched`` flag.
        """
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO players (player_key, full_name, position, team, matched)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (player_key) DO UPDATE SET
                    full_name = excluded.full_name,
                    position  = excluded.position,
                    team      = excluded.team,
                    matched   = excluded.matched
                """,
                [
                    row["player_key"],
                    row["full_name"],
                    row["position"],
                    row["team"],
                    row["matched"],
                ],
            )
            self.conn.execute(
                """
                INSERT INTO projections
                    (player_key, season, source, scope, native_id, stats_json, src_pts_ppr)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (player_key, season, source, scope) DO UPDATE SET
                    native_id   = excluded.native_id,
                    stats_json  = excluded.stats_json,
                    src_pts_ppr = excluded.src_pts_ppr
                """,
                [
                    row["player_key"],
                    row["season"],
                    row["source"],
                    row["scope"],
                    row["native_id"],
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

    # --- ADP (name-resolved source values, not computed) ------------------
    def upsert_adp(self, rows: list[dict[str, Any]]) -> None:
        """Insert-or-replace ADP rows, keyed by ``(player_key, season, source)``.

        ADP is a *source* value (stored), unlike points (computed at read time).
        Rows carry their own name/position/team so unmatched (``ffc:``-fallback)
        entries stay displayable without a ``players`` row.
        """
        cols = ", ".join(_ADP_COLUMNS)
        placeholders = ", ".join("?" * len(_ADP_COLUMNS))
        updates = ", ".join(
            f"{c} = excluded.{c}"
            for c in _ADP_COLUMNS
            if c not in ("player_key", "season", "source")
        )
        for row in rows:
            self.conn.execute(
                f"""
                INSERT INTO adp ({cols}) VALUES ({placeholders})
                ON CONFLICT (player_key, season, source) DO UPDATE SET {updates}
                """,
                [row.get(c) for c in _ADP_COLUMNS],
            )

    def delete_adp(self, season: int, source: str = "ffc") -> None:
        """Remove a ``(season, source)`` ADP slice so a re-ingest mirrors the
        source rather than unioning with stale rows."""
        self.conn.execute(
            "DELETE FROM adp WHERE season = ? AND source = ?",
            [season, source],
        )

    # --- league state ----------------------------------------------------
    def replace_league_state(self, bundle: Any) -> dict[str, int]:
        """Atomically mirror a validated league bundle's state for its season."""
        league = bundle.league
        settings = bundle.settings
        season = league["season"]
        resolved = self.resolve_batch(
            "yahoo", [p["yahoo_player_id"] for r in bundle.rosters for p in r["players"]]
        )
        rows: list[dict[str, Any]] = []
        for roster in bundle.rosters:
            for player in roster["players"]:
                match = resolved.get(player["yahoo_player_id"])
                rows.append(
                    {
                        **player,
                        "team_key": roster["team_key"],
                        "week": roster["week"],
                        "player_key": match["player_key"]
                        if match
                        else f"yahoo:{player['yahoo_player_id']}",
                        "matched": bool(match),
                        "full_name": match["full_name"] if match else player["name"],
                        "position": match["position"] if match else player["primary_position"],
                        "team": match["team"] if match else player["nfl_team"],
                    }
                )
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute("DELETE FROM league_settings WHERE season = ?", [season])
            self.conn.execute(
                """INSERT INTO league_settings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    season,
                    league["league_id"],
                    league["league_key"],
                    league["name"],
                    league["current_week"],
                    league["num_teams"],
                    bundle.data["source"],
                    bundle.data["synced_at"],
                    json.dumps(settings["roster_slots"]),
                    json.dumps(settings["scoring_rules"]),
                    json.dumps(settings["unmapped_scoring_rules"]),
                    json.dumps(settings["provider_settings"]),
                ],
            )
            self.conn.execute("DELETE FROM league_teams WHERE season = ?", [season])
            for team in bundle.teams:
                self.conn.execute(
                    "INSERT INTO league_teams VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        season,
                        team["team_key"],
                        team["team_id"],
                        team["name"],
                        json.dumps(team["managers"]),
                        team["is_user_team"],
                    ],
                )
            self.conn.execute(
                "DELETE FROM league_rosters WHERE season = ? AND week = ?",
                [season, league["current_week"]],
            )
            for row in rows:
                self.conn.execute(
                    "INSERT INTO league_rosters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        season,
                        row["week"],
                        row["team_key"],
                        row["yahoo_player_id"],
                        row["yahoo_player_key"],
                        row["full_name"],
                        row["team"],
                        row["position"],
                        json.dumps(row["eligible_positions"]),
                        row["selected_position"],
                        row["player_key"],
                        row["matched"],
                    ],
                )
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
        return {
            "teams": len(bundle.teams),
            "players": len(rows),
            "matched": sum(r["matched"] for r in rows),
            "unmatched": sum(not r["matched"] for r in rows),
        }

    def league_context(self, season: int) -> dict[str, Any] | None:
        cursor = self.conn.execute("SELECT * FROM league_settings WHERE season = ?", [season])
        values = cursor.fetchone()
        if not values:
            return None
        row = dict(zip([c[0] for c in cursor.description], values, strict=True))
        for key in ("roster_slots", "scoring_rules", "unmapped_scoring_rules", "provider_settings"):
            row[key] = json.loads(row.pop(f"{key}_json"))
        return row

    def league_teams(self, season: int) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM league_teams WHERE season = ? ORDER BY team_key", [season]
        )
        rows = [
            dict(zip([c[0] for c in cursor.description], values, strict=True))
            for values in cursor.fetchall()
        ]
        for row in rows:
            row["managers"] = json.loads(row.pop("managers_json"))
        return rows

    def league_roster_rows(self, season: int, week: int | None = None) -> list[dict[str, Any]]:
        if week is None:
            context = self.league_context(season)
            if context is None:
                return []
            week = context["current_week"]
        cursor = self.conn.execute(
            "SELECT * FROM league_rosters WHERE season = ? AND week = ? "
            "ORDER BY team_key, yahoo_player_id",
            [season, week],
        )
        rows = [
            dict(zip([c[0] for c in cursor.description], values, strict=True))
            for values in cursor.fetchall()
        ]
        for row in rows:
            row["eligible_positions"] = json.loads(row.pop("eligible_positions_json"))
        return rows

    def adp_rows(self, season: int, source: str = "ffc") -> list[dict[str, Any]]:
        """Return stored ADP rows for a season/source as plain dicts."""
        cursor = self.conn.execute(
            f"SELECT {', '.join(_ADP_COLUMNS)} FROM adp WHERE season = ? AND source = ?",
            [season, source],
        )
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, values, strict=True)) for values in cursor.fetchall()]

    def crosswalk_rows(self) -> list[dict[str, Any]]:
        """Read the crosswalk spine (for name-based ADP resolution).

        Keeps ``duckdb`` confined to this module — the name matcher works over
        plain dicts.
        """
        cursor = self.conn.execute("SELECT player_key, full_name, position, team FROM crosswalk")
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, values, strict=True)) for values in cursor.fetchall()]

    # --- reads ------------------------------------------------------------
    def has_season(self, season: int, source: str | None = None, scope: str | None = None) -> bool:
        query = "SELECT COUNT(*) FROM projections WHERE season = ?"
        params: list[Any] = [season]
        if source is not None:
            query += " AND source = ?"
            params.append(source)
        if scope is not None:
            query += " AND scope = ?"
            params.append(scope)
        result = self.conn.execute(query, params).fetchone()
        return bool(result and result[0] > 0)

    def has_crosswalk(self) -> bool:
        result = self.conn.execute("SELECT COUNT(*) FROM crosswalk").fetchone()
        return bool(result and result[0] > 0)

    def has_stale_resolution(self, season: int, source: str) -> bool:
        """True if any stored ``(season, source)`` row no longer matches how it
        would resolve against the current crosswalk.

        For each row the expected key is the crosswalk match if the native id is
        present, else the ``source:native_id`` fallback. A row is stale when its
        stored ``player_key`` differs from that, which covers three cases:

        - a late crosswalk now resolves a fallback row to a canonical key;
        - a native id was reassigned to a different canonical key upstream;
        - a native id **disappeared** from a refreshed crosswalk, so a row still
          stored under its old canonical key must fall back to unmatched.

        A ``LEFT JOIN`` (not inner) is required so the disappeared case is seen;
        an inner join drops exactly the rows whose id is gone. Legitimately
        unmatched players already sit on their fallback key, so they equal the
        expected key and don't trigger a needless re-ingest.

        Scoped to ``season`` because that's the only slice ``_finalize`` re-ingests
        to heal a stale row; flagging a weekly-scope row (slice 9) would loop the
        seasonal re-ingest forever without ever fixing it.
        """
        column = self._source_column(source)
        result = self.conn.execute(
            f"""
            SELECT COUNT(*)
            FROM projections p
            LEFT JOIN crosswalk c ON c.{column} = p.native_id
            WHERE p.season = ? AND p.source = ? AND p.scope = 'season'
              AND p.player_key <> COALESCE(c.player_key, ? || ':' || p.native_id)
            """,
            [season, source, source],
        ).fetchone()
        return bool(result and result[0] > 0)

    def projection_rows(
        self,
        season: int,
        position: str | None = None,
        source: str | None = "sleeper",
        scope: str = "season",
    ) -> list[dict[str, Any]]:
        """Return joined player + projection rows (stats decoded to dicts).

        ``source=None`` returns every source for the season/scope — the shape
        consensus pivots over. A concrete source keeps single-source behavior.
        """
        query = """
            SELECT p.player_key, pl.full_name, pl.position, pl.team, pl.matched,
                   p.season, p.source, p.native_id, p.stats_json, p.src_pts_ppr
            FROM projections p
            JOIN players pl ON pl.player_key = p.player_key
            WHERE p.season = ? AND p.scope = ?
        """
        params: list[Any] = [season, scope]
        if source is not None:
            query += " AND p.source = ?"
            params.append(source)
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
