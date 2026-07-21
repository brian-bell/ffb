"""Ingest wiring: snapshot -> parse -> store, idempotent and offline-capable."""

import json
from pathlib import Path

from ffb.ingest import ensure_ingested
from ffb.snapshot import SnapshotCache
from ffb.sources.sleeper import snapshot_key

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper_projections_sample.json"


def _prime_snapshot(snap_dir: Path):
    """Pre-write the raw fixture as if a real fetch had happened."""
    cache = SnapshotCache(snap_dir)
    raw = json.loads(FIXTURE.read_text())
    cache.get_json(snapshot_key(2024), lambda: raw)
    return cache


def test_ingest_from_snapshot_offline(store, tmp_path):
    cache = _prime_snapshot(tmp_path / "snap")

    def no_network():
        raise AssertionError("must not fetch when snapshot exists")

    ensure_ingested(store, cache, season=2024, fetch=no_network)
    assert store.has_season(2024)
    assert len(store.projection_rows(2024, position="RB")) == 3


def test_ingest_is_idempotent(store, tmp_path):
    cache = _prime_snapshot(tmp_path / "snap")
    ensure_ingested(store, cache, season=2024, fetch=lambda: [])
    ensure_ingested(store, cache, season=2024, fetch=lambda: [])  # no-op
    assert len(store.projection_rows(2024, position="RB")) == 3


def test_refresh_reingests(store, tmp_path):
    cache = _prime_snapshot(tmp_path / "snap")
    ensure_ingested(store, cache, season=2024, fetch=lambda: [])
    # refresh forces re-fetch; feed a smaller dataset to prove it re-ran.
    henry_only = [json.loads(FIXTURE.read_text())[0]]
    ensure_ingested(store, cache, season=2024, refresh=True, fetch=lambda: henry_only)
    assert len(store.projection_rows(2024, position="RB")) == 1
