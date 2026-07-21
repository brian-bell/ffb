"""Snapshot cache: fetch once, replay offline. This is a product feature."""

import json

import pytest

from ffb.snapshot import SnapshotCache


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
