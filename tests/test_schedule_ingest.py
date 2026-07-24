"""Schedule ingest: snapshot -> parse byes -> store mirror (offline, every run)."""

import json
from pathlib import Path

import pytest

from ffb.ingest import ensure_schedule_ingested
from ffb.snapshot import SnapshotCache
from ffb.sources.schedule import snapshot_key

SCHEDULE = Path(__file__).parent / "fixtures" / "schedule_sample.json"


def _prime(snap_dir: Path):
    cache = SnapshotCache(snap_dir)
    cache.get_json(snapshot_key(2026), lambda: json.loads(SCHEDULE.read_text()))
    return cache


def _no_network():
    raise AssertionError("must not fetch when snapshot exists")


def test_schedule_ingest_offline_from_snapshot(store, tmp_path):
    cache = _prime(tmp_path / "snap")

    recon = ensure_schedule_ingested(store, cache, season=2026, fetch=_no_network)

    byes = {r["team"]: r["bye"] for r in store.team_bye_rows(2026)}
    assert len(byes) == 32
    assert byes["KCC"] == 2
    assert byes["LAR"] == 3
    assert byes["BAL"] == 4
    assert recon.source == "schedule"
    assert recon.n_rows == 32
    assert recon.matched == 32
    assert recon.unmatched == 0


def test_schedule_ingest_reparses_every_run(store, tmp_path):
    cache = _prime(tmp_path / "snap")
    ensure_schedule_ingested(store, cache, season=2026, fetch=_no_network)
    store.conn.execute("DELETE FROM team_byes WHERE team = 'KCC'")

    ensure_schedule_ingested(store, cache, season=2026, fetch=_no_network)

    byes = {r["team"]: r["bye"] for r in store.team_bye_rows(2026)}
    assert byes["KCC"] == 2  # plain re-run restores the mirror, no refresh needed


def test_schedule_ingest_empty_parse_raises(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(snapshot_key(2026), lambda: [])  # bad pull cached without a gate

    with pytest.raises(ValueError, match="missing byes"):
        ensure_schedule_ingested(store, cache, season=2026, fetch=_no_network)


def test_schedule_ingest_rejects_partial_snapshot_and_retains_byes(store, tmp_path):
    # A truncated pull that still parses some teams must not replace a complete
    # mirror: the full canonical team set is required before any write.
    good = _prime(tmp_path / "good")
    ensure_schedule_ingested(store, good, season=2026, fetch=_no_network)

    partial_raw = [
        g
        for g in json.loads(SCHEDULE.read_text())
        if isinstance(g, dict) and "BAL" not in (g.get("home_team"), g.get("away_team"))
    ]
    partial = SnapshotCache(tmp_path / "partial")
    partial.get_json(snapshot_key(2026), lambda: partial_raw)

    with pytest.raises(ValueError, match="BAL"):
        ensure_schedule_ingested(store, partial, season=2026, fetch=_no_network)

    assert len(store.team_bye_rows(2026)) == 32  # previous mirror untouched
