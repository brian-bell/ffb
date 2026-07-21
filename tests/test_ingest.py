"""Ingest wiring: snapshot -> parse -> resolve -> store, idempotent & offline."""

import json
from pathlib import Path

from ffb.ingest import ensure_crosswalk, ensure_espn_ingested, ensure_ingested
from ffb.snapshot import SnapshotCache
from ffb.sources.crosswalk import snapshot_key as xwalk_key
from ffb.sources.espn import snapshot_key as espn_key
from ffb.sources.sleeper import snapshot_key

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper_projections_sample.json"
XWALK = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"
ESPN = Path(__file__).parent / "fixtures" / "espn_projections_sample.json"


def _prime_snapshot(snap_dir: Path):
    """Pre-write the raw fixture as if a real fetch had happened."""
    cache = SnapshotCache(snap_dir)
    raw = json.loads(FIXTURE.read_text())
    cache.get_json(snapshot_key(2024), lambda: raw)
    return cache


def _no_network():
    raise AssertionError("must not fetch when snapshot exists")


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


def test_ensure_crosswalk_loads_offline_and_is_idempotent(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(xwalk_key(), lambda: json.loads(XWALK.read_text()))  # prime
    ensure_crosswalk(store, cache, fetch=_no_network)
    assert store.resolve("sleeper", "3198") == "12626"  # Derrick Henry
    # Second call short-circuits on the loaded crosswalk (no refetch).
    ensure_crosswalk(store, cache, fetch=_no_network)


def test_crosswalk_refresh_mirrors_source(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(xwalk_key(), lambda: json.loads(XWALK.read_text()))  # prime
    ensure_crosswalk(store, cache, fetch=_no_network)
    assert store.resolve("sleeper", "3198") == "12626"  # Derrick Henry

    # Next pull no longer carries Henry (id removed/reassigned upstream). A
    # refresh must mirror the source, not union with the stale snapshot, or the
    # old mapping keeps resolving to the wrong player.
    only_chase = [json.loads(XWALK.read_text())[1]]  # Ja'Marr Chase only
    ensure_crosswalk(store, cache, refresh=True, fetch=lambda: only_chase)
    assert store.resolve("sleeper", "3198") is None  # stale Henry mapping gone
    assert store.resolve("sleeper", "7564") == "13971"  # Chase still resolves


def test_crosswalk_refresh_preserves_spine_on_empty_pull(store, tmp_path):
    cache = SnapshotCache(tmp_path / "snap")
    cache.get_json(xwalk_key(), lambda: json.loads(XWALK.read_text()))  # prime
    ensure_crosswalk(store, cache, fetch=_no_network)
    assert store.resolve("sleeper", "3198") == "12626"

    # A transient upstream hiccup parses to zero rows. A refresh must not wipe a
    # usable spine over an empty pull — keep the existing mappings.
    n = ensure_crosswalk(store, cache, refresh=True, fetch=lambda: [])
    assert n == 0
    assert store.resolve("sleeper", "3198") == "12626"  # spine preserved


def test_ingest_resolves_matched_players_to_canonical_key(store, tmp_path, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    cache = _prime_snapshot(tmp_path / "snap")
    recon = ensure_ingested(store, cache, season=2024, fetch=_no_network)
    assert recon.matched == 1  # only Henry (sleeper 3198) is in the crosswalk fixture
    henry = next(r for r in store.projection_rows(2024, position="RB") if r["native_id"] == "3198")
    assert henry["player_key"] == "12626"
    assert henry["matched"] is True


def test_unmatched_players_still_ingested_and_reported(store, tmp_path, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    cache = _prime_snapshot(tmp_path / "snap")
    recon = ensure_ingested(store, cache, season=2024, fetch=_no_network)
    rbs = store.projection_rows(2024, position="RB")
    assert len(rbs) == 3  # nobody dropped
    assert [r for r in rbs if not r["matched"]]  # the crosswalk misses are present
    assert recon.unmatched == recon.n_rows - recon.matched
    assert recon.unmatched_names  # names captured for the miss report


def test_self_heals_after_late_crosswalk(store, tmp_path, crosswalk_rows):
    cache = _prime_snapshot(tmp_path / "snap")
    # First ingest with no crosswalk -> Henry lands under a fallback key.
    ensure_ingested(store, cache, season=2024, fetch=_no_network)
    henry = next(r for r in store.projection_rows(2024, position="RB") if r["native_id"] == "3198")
    assert henry["player_key"] == "sleeper:3198"
    assert henry["matched"] is False

    # Crosswalk arrives late; the next plain ingest self-heals — it detects the
    # now-resolvable fallback row and re-resolves it offline from the snapshot,
    # without a refresh flag.
    store.upsert_crosswalk(crosswalk_rows)
    ensure_ingested(store, cache, season=2024, fetch=_no_network)
    henry = next(r for r in store.projection_rows(2024, position="RB") if r["native_id"] == "3198")
    assert henry["player_key"] == "12626"
    assert henry["matched"] is True


def test_self_heals_when_crosswalk_mapping_disappears(store, tmp_path, crosswalk_rows):
    cache = _prime_snapshot(tmp_path / "snap")
    store.upsert_crosswalk(crosswalk_rows)
    # First ingest with the crosswalk -> Henry (sleeper 3198) is matched.
    ensure_ingested(store, cache, season=2024, fetch=_no_network)
    henry = next(r for r in store.projection_rows(2024, position="RB") if r["native_id"] == "3198")
    assert henry["player_key"] == "12626"
    assert henry["matched"] is True

    # The crosswalk is refreshed and Henry's mapping disappears upstream (removed,
    # not reassigned) — e.g. a bare --refresh or a refresh for another season that
    # doesn't re-ingest this slice. A later plain ingest must still self-heal: the
    # stale canonical match falls back to a source key and is reported unmatched,
    # instead of lingering because an inner join no longer sees the dropped id.
    store.replace_crosswalk([r for r in crosswalk_rows if r["sleeper_id"] != "3198"])
    ensure_ingested(store, cache, season=2024, fetch=_no_network)
    henry = next(r for r in store.projection_rows(2024, position="RB") if r["native_id"] == "3198")
    assert henry["player_key"] == "sleeper:3198"
    assert henry["matched"] is False


def test_espn_self_heals_after_late_crosswalk(store, tmp_path, crosswalk_rows):
    cache = _prime_snapshot(tmp_path / "snap")
    cache.get_json(espn_key(2024), lambda: json.loads(ESPN.read_text()))
    # Both sources ingested with NO crosswalk -> everything under fallback keys.
    ensure_ingested(store, cache, season=2024, fetch=_no_network)
    ensure_espn_ingested(store, cache, season=2024, fetch=_no_network)
    henry_espn = next(
        r for r in store.projection_rows(2024, source="espn") if r["native_id"] == "3043078"
    )
    assert henry_espn["player_key"] == "espn:3043078"

    # Crosswalk arrives; a later ESPN ingest self-heals even though Sleeper was
    # the source that first saw the crosswalk. Both Henry rows collapse to one key.
    store.upsert_crosswalk(crosswalk_rows)
    ensure_espn_ingested(store, cache, season=2024, fetch=_no_network)
    henry_espn = next(
        r for r in store.projection_rows(2024, source="espn") if r["native_id"] == "3043078"
    )
    assert henry_espn["player_key"] == "12626"
    assert henry_espn["matched"] is True


def test_season_ingest_not_skipped_when_only_weekly_rows_exist(store, tmp_path):
    cache = _prime_snapshot(tmp_path / "snap")
    # A weekly-scope row exists for sleeper/2024 but the season slice does not.
    # has_season counts every scope, so the skip gate must scope to the season
    # slice or it would treat the source as present and never ingest the season
    # snapshot. Weekly ingest is slice 9; this guards the season path meanwhile.
    store.upsert_projections(
        [
            {
                "player_key": "sleeper:week-ghost",
                "season": 2024,
                "source": "sleeper",
                "scope": "week1",
                "native_id": "week-ghost",
                "full_name": "Weekly Ghost",
                "position": "RB",
                "team": "FA",
                "matched": False,
                "stats": {},
                "src_pts_ppr": None,
            }
        ]
    )
    recon = ensure_ingested(store, cache, season=2024, fetch=_no_network)
    assert recon.n_rows > 0  # the season snapshot was actually ingested
    assert len(store.projection_rows(2024, position="RB")) == 3  # season RBs present


def test_ensure_espn_ingested_resolves_and_coexists_with_sleeper(store, tmp_path, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    cache = _prime_snapshot(tmp_path / "snap")
    cache.get_json(espn_key(2024), lambda: json.loads(ESPN.read_text()))

    ensure_ingested(store, cache, season=2024, fetch=_no_network)  # sleeper first
    recon = ensure_espn_ingested(store, cache, season=2024, fetch=_no_network)

    # Henry (3043078) + Chase (4362628) are in the crosswalk fixture; Allen isn't.
    assert recon.matched == 2
    espn_rows = store.projection_rows(2024, source="espn")
    henry = next(r for r in espn_rows if r["native_id"] == "3043078")
    assert henry["player_key"] == "12626"  # same canonical key as the Sleeper Henry
    # Ingesting ESPN must not delete the Sleeper slice.
    assert store.has_season(2024, source="sleeper")
    assert store.has_season(2024, source="espn")
