"""Schedule ingest: snapshot -> parse byes -> store mirror (offline, every run)."""

import json
from pathlib import Path

import pytest

from ffb.ingest import ensure_schedule_ingested
from ffb.outcomes import select_scoring_facts
from ffb.snapshot import SnapshotCache
from ffb.sources import schedule
from ffb.sources.schedule import snapshot_key

SCHEDULE = Path(__file__).parent / "fixtures" / "schedule_sample.json"


def _prime(snap_dir: Path):
    cache = SnapshotCache(snap_dir)
    cache.get_json(snapshot_key(2024), lambda: json.loads(SCHEDULE.read_text()))
    return cache


def _no_network():
    raise AssertionError("must not fetch when snapshot exists")


def test_schedule_ingest_offline_from_snapshot(store, tmp_path):
    cache = _prime(tmp_path / "snap")

    recon = ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)

    byes = {r["team"]: r["bye"] for r in store.team_bye_rows(2024)}
    assert len(byes) == 32
    assert byes["KCC"] == 2
    assert byes["LAR"] == 3
    assert byes["BAL"] == 4
    stored = store.schedule_game_rows(2024)
    games = {(r["week"], r["home_team"], r["away_team"]) for r in stored}
    assert (1, "BAL", "BUF") in games
    assert (1, "JAC", "KCC") in games
    assert all(r["gameday"] is None and r["gametime"] is None for r in stored)
    assert recon.source == "schedule"
    assert recon.n_rows == 32
    assert recon.matched == 32
    assert recon.unmatched == 0


def test_schedule_ingest_reparses_every_run(store, tmp_path):
    cache = _prime(tmp_path / "snap")
    ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)
    store.conn.execute("DELETE FROM team_byes WHERE team = 'KCC'")
    store.conn.execute("DELETE FROM schedule_games WHERE home_team = 'BAL'")

    ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)

    byes = {r["team"]: r["bye"] for r in store.team_bye_rows(2024)}
    assert byes["KCC"] == 2  # plain re-run restores the mirror, no refresh needed
    games = {(r["week"], r["home_team"], r["away_team"]) for r in store.schedule_game_rows(2024)}
    assert (1, "BAL", "BUF") in games


def test_schedule_ingest_empty_parse_raises(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(snapshot_key(2024), lambda: [])  # bad pull cached without a gate

    with pytest.raises(ValueError, match="missing byes"):
        ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)


def test_schedule_ingest_rejects_partial_snapshot_and_retains_byes(store, tmp_path):
    # A truncated pull that still parses some teams must not replace a complete
    # mirror: the full canonical team set is required before any write.
    good = _prime(tmp_path / "good")
    ensure_schedule_ingested(store, good, season=2024, fetch=_no_network)

    partial_raw = [
        g
        for g in json.loads(SCHEDULE.read_text())
        if isinstance(g, dict) and "BAL" not in (g.get("home_team"), g.get("away_team"))
    ]
    partial = SnapshotCache(tmp_path / "partial")
    partial.get_json(snapshot_key(2024), lambda: partial_raw)

    with pytest.raises(ValueError, match="BAL"):
        ensure_schedule_ingested(store, partial, season=2024, fetch=_no_network)

    assert len(store.team_bye_rows(2024)) == 32  # previous mirror untouched


def _with_week1_kickoff() -> list:
    raw = json.loads(SCHEDULE.read_text())
    for game in raw:
        if isinstance(game, dict) and game.get("week") == 1 and game.get("home_team") == "JAX":
            game["gameday"] = "2024-09-05"
            game["gametime"] = "20:20"
    return raw


def test_schedule_ingest_populates_gameday_and_gametime(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(snapshot_key(2024), lambda: _with_week1_kickoff())

    ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)

    rows = store.schedule_game_rows(2024)
    kc = next(row for row in rows if row["week"] == 1 and row["away_team"] == "KCC")
    assert kc["home_team"] == "JAC"
    assert kc["gameday"] == "2024-09-05"
    assert kc["gametime"] == "20:20"
    other = next(row for row in rows if row["week"] == 1 and row["home_team"] == "BAL")
    assert other["gameday"] is None
    assert other["gametime"] is None


def test_schedule_refresh_rejects_a_payload_missing_kickoff_times(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(snapshot_key(2024), lambda: _with_week1_kickoff())
    ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)
    before = cache.read_json(snapshot_key(2024))
    stored = store.schedule_game_rows(2024)

    with pytest.raises(ValueError, match="missing gameday/gametime"):
        ensure_schedule_ingested(
            store,
            cache,
            season=2024,
            refresh=True,
            fetch=lambda: json.loads(SCHEDULE.read_text()),
        )

    assert cache.read_json(snapshot_key(2024)) == before
    assert store.schedule_game_rows(2024) == stored


def test_schedule_refresh_rejects_one_kickoff_field_or_zero_timed_games(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    untimed = json.loads(SCHEDULE.read_text())
    cache.get_json(snapshot_key(2024), lambda: untimed)
    ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)
    before = cache.read_json(snapshot_key(2024))
    day_only = [
        dict(game, gameday="2024-09-05") if isinstance(game, dict) else game for game in untimed
    ]
    both_untimed = [
        dict(game, gameday="2024-09-05", gametime="NA") if isinstance(game, dict) else game
        for game in untimed
    ]

    for payload in (day_only, both_untimed):
        with pytest.raises(ValueError, match="missing gameday/gametime"):
            ensure_schedule_ingested(
                store,
                cache,
                season=2024,
                refresh=True,
                fetch=lambda payload=payload: payload,
            )
        assert cache.read_json(snapshot_key(2024)) == before


def test_schedule_refresh_replaces_a_malformed_snapshot(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    path = cache.root / f"{snapshot_key(2024)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")
    called = {"n": 0}

    def fetch():
        called["n"] += 1
        return _with_week1_kickoff()

    with pytest.raises(json.JSONDecodeError):
        ensure_schedule_ingested(store, cache, season=2024, fetch=fetch)
    assert called["n"] == 0

    ensure_schedule_ingested(store, cache, season=2024, refresh=True, fetch=fetch)

    assert called["n"] == 1
    kc = next(
        row
        for row in store.schedule_game_rows(2024)
        if row["week"] == 1 and row["away_team"] == "KCC"
    )
    assert kc["gameday"] == "2024-09-05"
    assert kc["gametime"] == "20:20"


def test_schedule_refresh_rejects_fewer_kickoff_times(store, tmp_path):
    timed = _with_week1_kickoff()
    for game in timed:
        if isinstance(game, dict) and game.get("week") == 1 and game.get("home_team") == "BAL":
            game["gameday"] = "2024-09-08"
            game["gametime"] = "13:00"
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(snapshot_key(2024), lambda: timed)
    ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)
    before = cache.read_json(snapshot_key(2024))
    stored = store.schedule_game_rows(2024)
    fewer = _with_week1_kickoff()

    with pytest.raises(ValueError, match="fewer than the cached snapshot"):
        ensure_schedule_ingested(store, cache, season=2024, refresh=True, fetch=lambda: fewer)

    assert cache.read_json(snapshot_key(2024)) == before
    assert store.schedule_game_rows(2024) == stored


def test_stored_kickoffs_score_the_pre_kickoff_ledger(store, tmp_path):
    # Thursday 2024-09-05 20:20 ET is 2024-09-06T00:20:00Z. The newest run is
    # after that kickoff; the earlier run is the one that may be scored.
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(snapshot_key(2024), lambda: _with_week1_kickoff())
    ensure_schedule_ingested(store, cache, season=2024, fetch=_no_network)

    kickoffs = schedule.team_kickoffs(store.schedule_game_rows(2024))
    assert kickoffs[(1, "KCC")] == "2024-09-06T00:20:00Z"

    def fact(generated_at: str, consensus: float) -> dict:
        return {
            "league_key": "414.l.1",
            "season": 2024,
            "week": 1,
            "native_id": "4046",
            "nfl_team": "KC",
            "consensus": consensus,
            "generated_at": generated_at,
        }

    scored = select_scoring_facts(
        [
            fact("2024-09-04T15:00:00Z", 10.0),
            fact("2024-09-06T12:00:00Z", 99.0),
        ],
        kickoffs,
    )

    assert [(row["generated_at"], row["consensus"]) for row in scored] == [
        ("2024-09-04T15:00:00Z", 10.0)
    ]
