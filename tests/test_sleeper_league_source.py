"""SleeperLeagueSource: snapshot keys, offline replay, no league_* writes."""

import json
from pathlib import Path

import httpx
import pytest

from ffb import config
from ffb.snapshot import SnapshotCache
from ffb.sources import sleeper_league as sl
from ffb.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "sleeper"
LEAGUE_ID = config.SLEEPER_LEAGUE_ID
USER_ID = config.SLEEPER_USER_ID


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def _routes():
    return {
        f"/v1/league/{LEAGUE_ID}": _load("league.json"),
        f"/v1/league/{LEAGUE_ID}/rosters": _load("rosters.json"),
        f"/v1/league/{LEAGUE_ID}/users": _load("users.json"),
        "/v1/state/nfl": _load("state_nfl.json"),
    }


def _transport(recorder=None, routes=None):
    routes = routes if routes is not None else _routes()

    def handler(request):
        if recorder is not None:
            recorder.append(request)
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, json={"error": f"no route {request.url.path}"})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _source(tmp_path, recorder=None, routes=None):
    cache = SnapshotCache(tmp_path / "snapshots")
    cache.put_json("sleeper/players_nfl", _load("players.json"))
    return sl.SleeperLeagueSource(
        league_id=LEAGUE_ID,
        user_id=USER_ID,
        cache=cache,
        transport=_transport(recorder, routes),
    )


def test_snapshot_keys_are_namespaced_under_sleeper():
    assert sl.snapshot_key(LEAGUE_ID, "league") == f"sleeper/league_{LEAGUE_ID}_league"
    assert sl.state_snapshot_key() == "sleeper/state_nfl"
    assert not hasattr(sl, "matchup_snapshot_key")
    assert not hasattr(sl, "fetch_matchups")


def test_fetch_snapshots_raw_pulls_and_maps_user_team(tmp_path):
    requests = []
    state = _source(tmp_path, recorder=requests).fetch(2026)
    assert state.league_key == config.SLEEPER_LEAGUE_KEY
    assert [team["is_user_team"] for team in state.teams].count(True) == 1
    names = {path.name for path in (tmp_path / "snapshots" / "sleeper").glob("*.json")}
    assert f"league_{LEAGUE_ID}_league.json" in names
    assert f"league_{LEAGUE_ID}_rosters.json" in names
    assert f"league_{LEAGUE_ID}_users.json" in names
    assert "state_nfl.json" in names
    assert any(request.url.path.endswith("/state/nfl") for request in requests)
    assert not any("/matchups/" in request.url.path for request in requests)


def test_second_fetch_replays_snapshots_without_network(tmp_path):
    _source(tmp_path).fetch(2026)

    def no_network(request):
        raise AssertionError("offline replay must not hit the network")

    replay = sl.SleeperLeagueSource(
        league_id=LEAGUE_ID,
        user_id=USER_ID,
        cache=SnapshotCache(tmp_path / "snapshots"),
        transport=httpx.MockTransport(no_network),
    )
    state = replay.fetch(2026)
    assert state.current_week == 2
    assert state.scoring.weights["rec"] == 1.0


def test_offline_and_refresh_cannot_combine(tmp_path):
    with pytest.raises(sl.SleeperLeagueError, match="--offline and --refresh"):
        _source(tmp_path).fetch(2026, offline=True, refresh=True)


def test_offline_miss_does_not_call_the_network(tmp_path):
    source = sl.SleeperLeagueSource(
        league_id=LEAGUE_ID,
        user_id=USER_ID,
        cache=SnapshotCache(tmp_path / "snapshots"),
        transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError())),
    )
    with pytest.raises(FileNotFoundError, match="offline"):
        source.fetch(2026, offline=True)


def test_failed_refresh_leaves_known_good_snapshots(tmp_path):
    source = _source(tmp_path)
    source.fetch(2026)
    before = {path: path.read_bytes() for path in (tmp_path / "snapshots").rglob("*.json")}
    routes = _routes()
    del routes[f"/v1/league/{LEAGUE_ID}/users"]
    broken = _source(tmp_path, routes=routes)
    with pytest.raises(httpx.HTTPStatusError):
        broken.fetch(2026, refresh=True)
    assert {path: path.read_bytes() for path in (tmp_path / "snapshots").rglob("*.json")} == before


def test_fetch_does_not_write_duckdb_league_state(tmp_path):
    _source(tmp_path).fetch(2026)
    store = Store(tmp_path / "ffb.duckdb")
    store.init_schema()
    assert store.league_context(2026) is None
    store.close()


def test_from_env_requires_both_sleeper_vars(tmp_path):
    cache = SnapshotCache(tmp_path / "snapshots")
    with pytest.raises(sl.SleeperLeagueError, match="FFB_SLEEPER_LEAGUE_ID"):
        sl.league_source_from_env(cache, {})
    with pytest.raises(sl.SleeperLeagueError, match="FFB_SLEEPER_USER_ID"):
        sl.league_source_from_env(cache, {"FFB_SLEEPER_LEAGUE_ID": LEAGUE_ID})
    source = sl.league_source_from_env(
        cache,
        {"FFB_SLEEPER_LEAGUE_ID": LEAGUE_ID, "FFB_SLEEPER_USER_ID": USER_ID},
    )
    assert source.league_id == LEAGUE_ID
    assert source.user_id == USER_ID
