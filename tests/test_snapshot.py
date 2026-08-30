"""Snapshot cache: fetch once, replay offline. This is a product feature."""

import json
import multiprocessing
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import BrokenBarrierError

import pytest

from ffb.snapshot import SnapshotAttemptThrottled, SnapshotCache


def _race_for_attempt_claim(cache_root, start, eligibility_barrier, release_fetch, events, index):
    """Exercise one provider attempt from an independent spawned process."""
    cache = SnapshotCache(Path(cache_root))
    original_last_attempt_at = cache.last_attempt_at
    original_put_json = cache.put_json

    def synchronized_last_attempt_at(key):
        value = original_last_attempt_at(key)
        try:
            eligibility_barrier.wait(timeout=1)
        except BrokenBarrierError:
            pass
        return value

    def staggered_put_json(key, data, *, mode=None):
        if key.endswith(".attempt") and index == 1:
            time.sleep(0.1)
        return original_put_json(key, data, mode=mode)

    cache.last_attempt_at = synchronized_last_attempt_at
    cache.put_json = staggered_put_json

    def fetch():
        events.put("fetch")
        release_fetch.wait(timeout=5)
        return {"players": []}

    start.wait(timeout=5)
    try:
        cache.get_json(
            "sleeper/players_nfl",
            fetch,
            refresh=True,
            attempt_interval=timedelta(days=1),
            now=datetime(2030, 8, 29, 14, 30, tzinfo=UTC),
        )
    except SnapshotAttemptThrottled:
        events.put("throttled")
    except BaseException as exc:
        events.put(f"error:{type(exc).__name__}:{exc}")
    else:
        events.put("completed")


def test_miss_calls_fetch_and_writes_file(tmp_path):
    cache = SnapshotCache(tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        return [{"player_id": "1"}]

    result = cache.get_json("sleeper/projections", fetch)

    assert result == [{"player_id": "1"}]
    assert calls == [1]
    written = (tmp_path / "sleeper" / "projections.json").read_text()
    assert json.loads(written) == [{"player_id": "1"}]


def test_hit_replays_without_calling_fetch(tmp_path):
    cache = SnapshotCache(tmp_path)
    cache.get_json("sleeper/projections", lambda: {"v": 1})  # populate

    def boom():
        raise AssertionError("fetch must not be called on a cache hit")

    result = cache.get_json("sleeper/projections", boom)
    assert result == {"v": 1}


def test_refresh_forces_fetch_and_overwrites(tmp_path):
    cache = SnapshotCache(tmp_path)
    cache.get_json("k", lambda: {"v": 1})
    result = cache.get_json("k", lambda: {"v": 2}, refresh=True)
    assert result == {"v": 2}
    assert cache.get_json("k", lambda: {"v": 3}) == {"v": 2}  # persisted


def test_missing_snapshot_offline_raises(tmp_path):
    cache = SnapshotCache(tmp_path)

    def fetch():
        raise RuntimeError("network disabled")

    with pytest.raises(RuntimeError):
        cache.get_json("never-fetched", fetch)


def test_has_reports_presence(tmp_path):
    cache = SnapshotCache(tmp_path)
    assert not cache.has("k")
    cache.get_json("k", lambda: {"v": 1})
    assert cache.has("k")


@pytest.mark.parametrize(
    "failure",
    [ConnectionError("network down"), json.JSONDecodeError("bad json", "{", 1)],
)
def test_failed_fetch_attempt_persists_and_throttles_new_cache_instance(tmp_path, failure):
    attempted_at = datetime(2030, 8, 29, 14, 30, tzinfo=UTC)
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(type(failure)):
        SnapshotCache(tmp_path).get_json(
            "sleeper/players_nfl",
            fetch,
            refresh=True,
            attempt_interval=timedelta(days=1),
            now=attempted_at,
        )

    with pytest.raises(SnapshotAttemptThrottled, match="last provider attempt"):
        SnapshotCache(tmp_path).get_json(
            "sleeper/players_nfl",
            fetch,
            refresh=True,
            attempt_interval=timedelta(days=1),
            now=attempted_at + timedelta(hours=1),
        )

    assert calls == 1


def test_attempt_claim_allows_only_one_fetch_across_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    eligibility_barrier = context.Barrier(2)
    release_fetch = context.Event()
    events = context.Queue()
    processes = [
        context.Process(
            target=_race_for_attempt_claim,
            args=(tmp_path, start, eligibility_barrier, release_fetch, events, index),
        )
        for index in range(2)
    ]

    for process in processes:
        process.start()
    start.set()
    try:
        initial_events = [events.get(timeout=5), events.get(timeout=5)]
    finally:
        release_fetch.set()
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert sorted(initial_events) == ["fetch", "throttled"]
    assert all(process.exitcode == 0 for process in processes)


def test_future_attempt_marker_is_repaired_and_allows_one_recovery(tmp_path):
    cache = SnapshotCache(tmp_path)
    attempted_at = datetime(2030, 8, 29, 14, 30, tzinfo=UTC)
    cache.put_json(
        "sleeper/players_nfl.attempt",
        {"last_attempt_at": "9999-12-31T23:59:59Z"},
    )
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return {"players": []}

    result = cache.get_json(
        "sleeper/players_nfl",
        fetch,
        refresh=True,
        attempt_interval=timedelta(days=1),
        now=attempted_at,
    )

    assert result == {"players": []}
    assert cache.last_attempt_at("sleeper/players_nfl") == attempted_at
    with pytest.raises(SnapshotAttemptThrottled, match="last provider attempt"):
        SnapshotCache(tmp_path).get_json(
            "sleeper/players_nfl",
            fetch,
            refresh=True,
            attempt_interval=timedelta(days=1),
            now=attempted_at + timedelta(hours=1),
        )
    assert calls == 1


def test_plausible_clock_rollback_throttles_without_rewriting_marker(tmp_path):
    cache = SnapshotCache(tmp_path)
    previous_attempt = datetime(2030, 8, 29, 14, 30, tzinfo=UTC)
    rolled_back_now = previous_attempt - timedelta(minutes=5)
    cache.put_json(
        "sleeper/players_nfl.attempt",
        {"last_attempt_at": previous_attempt.isoformat().replace("+00:00", "Z")},
    )
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return {"players": []}

    with pytest.raises(SnapshotAttemptThrottled, match="last provider attempt"):
        cache.get_json(
            "sleeper/players_nfl",
            fetch,
            refresh=True,
            attempt_interval=timedelta(days=1),
            now=rolled_back_now,
        )

    assert calls == 0
    assert cache.last_attempt_at("sleeper/players_nfl") == previous_attempt


def test_future_attempt_at_skew_threshold_throttles_without_rewriting_marker(tmp_path):
    cache = SnapshotCache(tmp_path)
    attempted_at = datetime(2030, 8, 29, 14, 30, tzinfo=UTC)
    previous_attempt = attempted_at + timedelta(days=1)
    cache.put_json(
        "sleeper/players_nfl.attempt",
        {"last_attempt_at": previous_attempt.isoformat().replace("+00:00", "Z")},
    )

    with pytest.raises(SnapshotAttemptThrottled, match="later than the current clock"):
        cache.get_json(
            "sleeper/players_nfl",
            lambda: pytest.fail("fetch must not run inside the skew threshold"),
            refresh=True,
            attempt_interval=timedelta(days=1),
            now=attempted_at,
        )

    assert cache.last_attempt_at("sleeper/players_nfl") == previous_attempt


def test_future_attempt_beyond_skew_threshold_repairs_and_claims(tmp_path):
    cache = SnapshotCache(tmp_path)
    attempted_at = datetime(2030, 8, 29, 14, 30, tzinfo=UTC)
    previous_attempt = attempted_at + timedelta(days=1, microseconds=1)
    cache.put_json(
        "sleeper/players_nfl.attempt",
        {"last_attempt_at": previous_attempt.isoformat().replace("+00:00", "Z")},
    )
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return {"players": []}

    result = cache.get_json(
        "sleeper/players_nfl",
        fetch,
        refresh=True,
        attempt_interval=timedelta(days=1),
        now=attempted_at,
    )

    assert result == {"players": []}
    assert calls == 1
    assert cache.last_attempt_at("sleeper/players_nfl") == attempted_at
