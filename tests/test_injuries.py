"""Sleeper player-status snapshot through identity, storage, and board join."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ffb.board import board_rows
from ffb.ingest import ensure_injuries_ingested
from ffb.snapshot import SnapshotCache
from ffb.sources.sleeper_players import canonical_status, parse_players

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = "2026-08-29T14:30:00Z"
ATTEMPT_NOW = datetime(2030, 8, 29, 14, 30, tzinfo=UTC)


def _raw_players():
    return json.loads((FIXTURES / "sleeper_players_injury_sample.json").read_text())


def test_sleeper_snapshot_resolves_persists_and_joins_optional_injury(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    cache = SnapshotCache(store.path.parent / "snapshots")

    result = ensure_injuries_ingested(
        store,
        cache,
        2026,
        fetched_at=FETCHED_AT,
        fetch=_raw_players,
    )

    assert (result.n_rows, result.matched, result.unmatched) == (6, 5, 1)
    injuries = {row["native_id"]: row for row in store.injury_rows(2026)}
    assert injuries["3198"] == {
        "player_key": "12626",
        "season": 2026,
        "source": "sleeper",
        "native_id": "3198",
        "raw_injury_status": "Questionable",
        "raw_roster_status": "Active",
        "status": "QUESTIONABLE",
        "fetched_at": FETCHED_AT,
        "matched": True,
    }
    assert injuries["7564"]["status"] == "IR"
    assert injuries["1264"]["status"] == "UNKNOWN"  # direct Sus beats roster PUP
    assert injuries["1516"]["status"] == "NFI"
    assert injuries["4960"]["status"] is None
    assert injuries["9999"]["matched"] is False

    consensus = [
        {
            "player_key": "12626",
            "full_name": "Derrick Henry",
            "position": "RB",
            "team": "BAL",
            "matched": True,
            "consensus": 100.0,
            "n": 2,
            "draftable": True,
        }
    ]
    rows = board_rows(
        consensus,
        [],
        injuries=store.injury_rows(2026),
        roster_slots={"RB": 1, "BN": 1},
        num_teams=1,
        tier_count={"RB": 1},
        pools={"RB": 10},
    )
    assert rows[0]["injury"] == {
        "status": "QUESTIONABLE",
        "fetched_at": FETCHED_AT,
    }


def test_missing_only_cache_calls_full_player_map_boundary_once(store, crosswalk_rows, tmp_path):
    store.upsert_crosswalk(crosswalk_rows)
    cache = SnapshotCache(tmp_path / "snapshots")
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return _raw_players()

    ensure_injuries_ingested(store, cache, 2026, fetch=fetch)
    ensure_injuries_ingested(store, cache, 2026, fetch=fetch)

    assert calls == 1


def test_refresh_does_not_refetch_a_player_map_younger_than_one_day(
    store, crosswalk_rows, tmp_path
):
    store.upsert_crosswalk(crosswalk_rows)
    cache = SnapshotCache(tmp_path / "snapshots")
    cache.put_json("sleeper/players_nfl", _raw_players())

    ensure_injuries_ingested(
        store,
        cache,
        2026,
        refresh=True,
        now=datetime.now(UTC),
        fetch=lambda: (_ for _ in ()).throw(AssertionError("unexpected second daily fetch")),
    )


def test_truncated_refresh_preserves_known_good_snapshot_and_rows(store, crosswalk_rows, tmp_path):
    store.upsert_crosswalk(crosswalk_rows)
    cache = SnapshotCache(tmp_path / "snapshots")
    ensure_injuries_ingested(
        store,
        cache,
        2026,
        now=ATTEMPT_NOW - timedelta(days=2),
        fetch=_raw_players,
    )
    snapshot_path = tmp_path / "snapshots" / "sleeper" / "players_nfl.json"
    snapshot_before = snapshot_path.read_text()
    rows_before = store.injury_rows(2026)
    truncated = {"3198": _raw_players()["3198"]}

    with pytest.raises(ValueError, match="coverage"):
        ensure_injuries_ingested(
            store,
            cache,
            2026,
            refresh=True,
            now=ATTEMPT_NOW,
            fetch=lambda: truncated,
        )

    assert snapshot_path.read_text() == snapshot_before
    assert store.injury_rows(2026) == rows_before


def test_rejected_recovery_attempt_throttles_second_same_day_refresh(
    store, crosswalk_rows, tmp_path
):
    store.upsert_crosswalk(crosswalk_rows)
    cache = SnapshotCache(tmp_path / "snapshots")
    ensure_injuries_ingested(
        store,
        cache,
        2026,
        now=ATTEMPT_NOW - timedelta(days=2),
        fetch=_raw_players,
    )
    snapshot_path = tmp_path / "snapshots" / "sleeper" / "players_nfl.json"
    snapshot_before = snapshot_path.read_bytes()
    metadata_before = cache.metadata("sleeper/players_nfl")
    rows_before = store.injury_rows(2026)
    truncated = {"3198": _raw_players()["3198"]}
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return truncated

    with pytest.raises(ValueError, match="coverage"):
        ensure_injuries_ingested(
            store,
            cache,
            2026,
            refresh=True,
            now=ATTEMPT_NOW,
            fetch=fetch,
        )
    with pytest.raises((ValueError, RuntimeError)) as second:
        ensure_injuries_ingested(
            store,
            cache,
            2026,
            refresh=True,
            now=ATTEMPT_NOW + timedelta(hours=1),
            fetch=fetch,
        )

    assert calls == 1
    assert "throttled" in str(second.value)
    assert snapshot_path.read_bytes() == snapshot_before
    assert cache.metadata("sleeper/players_nfl") == metadata_before
    assert store.injury_rows(2026) == rows_before


def test_rejected_recovery_can_attempt_again_at_24_hour_boundary(store, crosswalk_rows, tmp_path):
    store.upsert_crosswalk(crosswalk_rows)
    cache = SnapshotCache(tmp_path / "snapshots")
    ensure_injuries_ingested(
        store,
        cache,
        2026,
        now=ATTEMPT_NOW - timedelta(days=2),
        fetch=_raw_players,
    )
    truncated = {"3198": _raw_players()["3198"]}
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return truncated

    for attempted_at in (ATTEMPT_NOW, ATTEMPT_NOW + timedelta(days=1)):
        with pytest.raises(ValueError, match="coverage"):
            ensure_injuries_ingested(
                store,
                cache,
                2026,
                refresh=True,
                now=attempted_at,
                fetch=fetch,
            )

    assert calls == 2


def test_global_attempt_throttle_persists_across_cache_instances_and_seasons(
    store, crosswalk_rows, tmp_path
):
    store.upsert_crosswalk(crosswalk_rows)
    root = tmp_path / "snapshots"
    first_cache = SnapshotCache(root)
    ensure_injuries_ingested(
        store,
        first_cache,
        2026,
        now=ATTEMPT_NOW - timedelta(days=2),
        fetch=_raw_players,
    )
    truncated = {"3198": _raw_players()["3198"]}
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return truncated

    with pytest.raises(ValueError, match="coverage"):
        ensure_injuries_ingested(
            store,
            first_cache,
            2026,
            refresh=True,
            now=ATTEMPT_NOW,
            fetch=fetch,
        )
    (root / "sleeper" / "players_nfl.json").write_text("{corrupt")

    second_cache = SnapshotCache(root)
    with pytest.raises(RuntimeError, match="throttled"):
        ensure_injuries_ingested(
            store,
            second_cache,
            2027,
            refresh=True,
            now=ATTEMPT_NOW + timedelta(hours=1),
            fetch=fetch,
        )

    assert calls == 1
    assert second_cache.last_attempt_at("sleeper/players_nfl") == ATTEMPT_NOW


def test_recent_corrupt_snapshot_allows_one_authorized_recovery_fetch(
    store, crosswalk_rows, tmp_path
):
    store.upsert_crosswalk(crosswalk_rows)
    cache = SnapshotCache(tmp_path / "snapshots")
    ensure_injuries_ingested(
        store,
        cache,
        2026,
        now=ATTEMPT_NOW - timedelta(days=2),
        fetch=_raw_players,
    )
    snapshot_path = tmp_path / "snapshots" / "sleeper" / "players_nfl.json"
    snapshot_path.write_text("{corrupt")
    recovered = _raw_players()
    recovered["3198"]["injury_status"] = "Out"
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return recovered

    ensure_injuries_ingested(
        store,
        cache,
        2026,
        refresh=True,
        now=ATTEMPT_NOW,
        fetch=fetch,
    )

    assert calls == 1
    assert json.loads(snapshot_path.read_text()) == recovered
    henry = next(row for row in store.injury_rows(2026) if row["native_id"] == "3198")
    assert henry["status"] == "OUT"


def test_canonical_status_vocabulary_and_precedence():
    assert canonical_status("Questionable", None) == "QUESTIONABLE"
    assert canonical_status("Doubtful", None) == "DOUBTFUL"
    assert canonical_status("Out", None) == "OUT"
    assert canonical_status("IR", None) == "IR"
    assert canonical_status("PUP", None) == "PUP"
    assert canonical_status(None, "Injured Reserve") == "IR"
    assert canonical_status("", "Physically Unable to Perform") == "PUP"
    assert canonical_status(None, "Non Football Injury") == "NFI"
    assert canonical_status("COV", "Injured Reserve") == "UNKNOWN"
    assert canonical_status("DNR", None) == "UNKNOWN"
    assert canonical_status("NA", None) == "UNKNOWN"
    assert canonical_status("Sus", None) == "UNKNOWN"
    assert canonical_status(None, "Active") is None
    assert canonical_status(None, None) is None


def test_player_map_key_is_authoritative_and_conflicting_inner_id_is_skipped(
    store, crosswalk_rows, tmp_path
):
    raw = {
        "3198": {
            "first_name": "Derrick",
            "last_name": "Henry",
            "position": "RB",
            "team": "BAL",
            "injury_status": "Out",
            "status": "Inactive",
        },
        "7564": {
            "player_id": "7564",
            "first_name": "Ja'Marr",
            "last_name": "Chase",
            "position": "WR",
            "team": "CIN",
            "injury_status": "Questionable",
            "status": "Active",
        },
        "1264": {
            "player_id": "3198",
            "first_name": "Conflicting",
            "last_name": "Identity",
            "position": "K",
            "team": "BAL",
            "injury_status": "PUP",
            "status": "Physically Unable to Perform",
        },
    }

    assert [row["native_id"] for row in parse_players(raw)] == ["3198", "7564"]

    store.upsert_crosswalk(crosswalk_rows)
    ensure_injuries_ingested(
        store,
        SnapshotCache(tmp_path / "snapshots"),
        2026,
        fetched_at=FETCHED_AT,
        fetch=lambda: raw,
    )
    stored = {row["native_id"]: row for row in store.injury_rows(2026)}
    assert set(stored) == {"3198", "7564"}
    assert stored["3198"]["status"] == "OUT"


def test_crosswalk_change_marks_persisted_injury_identity_stale(store, crosswalk_rows, tmp_path):
    store.upsert_crosswalk(crosswalk_rows)
    ensure_injuries_ingested(
        store,
        SnapshotCache(tmp_path / "snapshots"),
        2026,
        fetched_at=FETCHED_AT,
        fetch=_raw_players,
    )
    assert store.has_stale_injury_resolution(2026) is False

    store.replace_crosswalk([row for row in crosswalk_rows if row["sleeper_id"] != "3198"])

    assert store.has_stale_injury_resolution(2026) is True
