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
    assert byes == {"KCC": 2, "SFO": 2, "LAR": 3, "PHI": 3, "DEN": 4, "MIA": 4}
    assert recon.source == "schedule"
    assert recon.n_rows == 6
    assert recon.matched == 6
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

    with pytest.raises(ValueError, match="no usable"):
        ensure_schedule_ingested(store, cache, season=2026, fetch=_no_network)
