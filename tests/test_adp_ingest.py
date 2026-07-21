"""ADP ingest: snapshot -> parse -> name-resolve -> store (offline, mirror)."""

import json
from pathlib import Path

import pytest

from ffb.ingest import ensure_adp_ingested
from ffb.snapshot import SnapshotCache
from ffb.sources.ffc import snapshot_key
from ffb.store import Store

FFC = Path(__file__).parent / "fixtures" / "ffc_adp_sample.json"


def _prime(snap_dir: Path):
    cache = SnapshotCache(snap_dir)
    cache.get_json(snapshot_key(2024), lambda: json.loads(FFC.read_text()))
    return cache


def _xrow(key, name, pos, team):
    return {
        "player_key": key,
        "sleeper_id": None,
        "espn_id": None,
        "yahoo_id": None,
        "gsis_id": None,
        "full_name": name,
        "position": pos,
        "team": team,
    }


# Crosswalk teams are nflverse/MFL style (SFO, KCC), matching FFC's aliased codes.
CROSSWALK = [
    _xrow("2434", "Christian McCaffrey", "RB", "SFO"),
    _xrow("13971", "Ja'Marr Chase", "WR", "CIN"),
    _xrow("11111", "Derrick Henry", "RB", "BAL"),
    _xrow(
        "22222", "Michael Pittman", "WR", "IND"
    ),  # matches "Michael Pittman Jr." after suffix strip
]


def _no_network():
    raise AssertionError("must not fetch when snapshot exists")


def test_ingest_resolves_matched_and_fallback_keys(store, tmp_path):
    store.upsert_crosswalk(CROSSWALK)
    cache = _prime(tmp_path / "snap")

    recon = ensure_adp_ingested(store, cache, season=2024, fetch=_no_network)

    rows = {r["native_id"]: r for r in store.adp_rows(2024)}
    # Matched: canonical key + matched flag.
    assert rows["2749"]["player_key"] == "2434"  # McCaffrey
    assert rows["2749"]["matched"] is True
    # Suffix-stripped match: "Michael Pittman Jr." -> crosswalk "Michael Pittman".
    assert rows["5310"]["player_key"] == "22222"
    assert rows["5310"]["matched"] is True
    # Crosswalk miss (DEF not in ff_playerids) -> ffc: fallback, never dropped.
    assert rows["9001"]["player_key"] == "ffc:9001"
    assert rows["9001"]["matched"] is False
    assert rows["9001"]["full_name"] == "San Francisco Defense"

    assert recon.source == "ffc"
    assert recon.matched == 4
    assert recon.unmatched == recon.n_rows - 4
    assert recon.unmatched_names  # surfaced for the footer


def test_ingest_self_heals_after_late_crosswalk(store, tmp_path):
    cache = _prime(tmp_path / "snap")
    # First run with NO crosswalk -> everything under ffc: fallback keys.
    ensure_adp_ingested(store, cache, season=2024, fetch=_no_network)
    mcc = next(r for r in store.adp_rows(2024) if r["native_id"] == "2749")
    assert mcc["player_key"] == "ffc:2749"
    assert mcc["matched"] is False

    # Crosswalk arrives; ADP always re-resolves from the cached snapshot (no
    # staleness detector, no --refresh needed).
    store.upsert_crosswalk(CROSSWALK)
    ensure_adp_ingested(store, cache, season=2024, fetch=_no_network)
    mcc = next(r for r in store.adp_rows(2024) if r["native_id"] == "2749")
    assert mcc["player_key"] == "2434"
    assert mcc["matched"] is True


def test_ingest_mirrors_snapshot_no_stale_rows(store, tmp_path):
    cache = _prime(tmp_path / "snap")
    ensure_adp_ingested(store, cache, season=2024, fetch=_no_network)
    before = len(store.adp_rows(2024))

    # A refresh returns a smaller pull; the slice must mirror it, not union.
    small = {
        "status": "Success",
        "players": [
            {
                "player_id": 2749,
                "name": "Christian McCaffrey",
                "position": "RB",
                "team": "SF",
                "adp": 1.4,
                "high": 1,
                "low": 5,
                "stdev": 0.7,
                "times_drafted": 1000,
                "bye": 9,
            }
        ],
    }
    ensure_adp_ingested(store, cache, season=2024, refresh=True, fetch=lambda: small)
    rows = store.adp_rows(2024)
    assert len(rows) == 1 < before
    assert rows[0]["native_id"] == "2749"


def test_invalid_pull_raises_but_preserves_snapshot(store, tmp_path):
    cache = _prime(tmp_path / "snap")
    ensure_adp_ingested(store, cache, season=2024, fetch=_no_network)

    # A transient bad --refresh (non-Success) is surfaced as a failure so the CLI
    # warns and drops ADP rather than silently serving the stale persisted slice
    # as current — but it must not overwrite the good on-disk snapshot.
    with pytest.raises(ValueError):
        ensure_adp_ingested(
            store, cache, season=2024, refresh=True, fetch=lambda: {"status": "Error"}
        )

    # Rebuild a fresh store offline: the on-disk snapshot must still be the good one.
    fresh = Store(tmp_path / "fresh.duckdb")
    fresh.init_schema()
    ensure_adp_ingested(fresh, cache, season=2024, fetch=_no_network)
    assert any(r["native_id"] == "2749" for r in fresh.adp_rows(2024))
    fresh.close()
