"""YahooLeagueSource: httpx fetch, snapshot integration, credential hygiene."""

import json
from pathlib import Path

import httpx
import pytest

from ffb.league import LeagueBundle
from ffb.snapshot import SnapshotCache
from ffb.sources import yahoo

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"
LEAGUE_KEY = "461.l.12345"
ACCESS_TOKEN = "sekret-access-token"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def _routes():
    return {
        f"/fantasy/v2/league/{LEAGUE_KEY}": _load("league_meta.json"),
        f"/fantasy/v2/league/{LEAGUE_KEY}/settings": _load("league_settings.json"),
        f"/fantasy/v2/league/{LEAGUE_KEY}/teams": _load("league_teams.json"),
        f"/fantasy/v2/team/{LEAGUE_KEY}.t.1/roster;week=2": _load("roster_team1.json"),
        f"/fantasy/v2/team/{LEAGUE_KEY}.t.2/roster;week=2": _load("roster_team2.json"),
    }


def _transport(recorder=None, routes=None):
    routes = routes if routes is not None else _routes()

    def handler(request):
        if recorder is not None:
            recorder.append(request)
        if request.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            return httpx.Response(401, json={"error": "unauthorized"})
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, json={"error": f"no route {request.url.path}"})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _source(tmp_path, recorder=None, routes=None, token_calls=None):
    def token_provider():
        if token_calls is not None:
            token_calls.append(1)
        return ACCESS_TOKEN

    return yahoo.YahooLeagueSource(
        league_key=LEAGUE_KEY,
        cache=SnapshotCache(tmp_path / "snapshots"),
        token_provider=token_provider,
        transport=_transport(recorder, routes),
    )


def test_fetch_returns_a_validated_bundle_and_snapshots_every_raw_pull(tmp_path):
    bundle = _source(tmp_path).fetch(2026)
    assert isinstance(bundle, LeagueBundle)
    assert bundle.league["league_key"] == LEAGUE_KEY
    assert {t["name"] for t in bundle.teams} == {"Brian's Best", "Rival Squad"}
    snapshots = sorted(p.name for p in (tmp_path / "snapshots" / "yahoo").glob("*.json"))
    assert len(snapshots) == 5  # meta, settings, teams, two rosters


def test_second_fetch_replays_snapshots_without_network_or_token(tmp_path):
    _source(tmp_path).fetch(2026)

    def no_network(request):
        raise AssertionError("offline replay must not hit the network")

    token_calls = []
    replay = yahoo.YahooLeagueSource(
        league_key=LEAGUE_KEY,
        cache=SnapshotCache(tmp_path / "snapshots"),
        token_provider=lambda: (_ for _ in ()).throw(AssertionError("no token needed offline")),
        transport=httpx.MockTransport(no_network),
    )
    bundle = replay.fetch(2026)
    assert bundle.league["num_teams"] == 2
    assert token_calls == []


def test_refresh_refetches_and_rewrites_snapshots(tmp_path):
    requests = []
    source = _source(tmp_path, recorder=requests)
    source.fetch(2026)
    first = len(requests)
    source.fetch(2026, refresh=True)
    assert len(requests) == first * 2


def test_failed_fetch_persists_no_snapshots_at_all(tmp_path):
    """Staged commit: a partial pull must never leave a partial snapshot set."""
    routes = _routes()
    del routes[f"/fantasy/v2/league/{LEAGUE_KEY}/teams"]
    source = _source(tmp_path, routes=routes)
    with pytest.raises(httpx.HTTPStatusError):
        source.fetch(2026)
    assert list((tmp_path / "snapshots").rglob("*.json")) == []


def test_failed_refresh_leaves_the_known_good_snapshot_set_untouched(tmp_path):
    source = _source(tmp_path)
    source.fetch(2026)
    before = {p: p.read_bytes() for p in (tmp_path / "snapshots").rglob("*.json")}
    assert len(before) == 5

    routes = _routes()
    del routes[f"/fantasy/v2/team/{LEAGUE_KEY}.t.2/roster;week=2"]
    broken = _source(tmp_path, routes=routes)
    with pytest.raises(httpx.HTTPStatusError):
        broken.fetch(2026, refresh=True)
    assert {p: p.read_bytes() for p in (tmp_path / "snapshots").rglob("*.json")} == before


def test_snapshots_are_owner_only(tmp_path):
    import stat

    _source(tmp_path).fetch(2026)
    for path in (tmp_path / "snapshots").rglob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"oops": "maintenance page"},
        # Wrapped like a real response but structurally unusable: the
        # resource-specific validator, not just the wrapper check, must gate.
        {"fantasy_content": {"league": [{}]}},
    ],
)
def test_bad_payload_is_not_persisted_over_known_good_snapshot(tmp_path, bad_payload):
    source = _source(tmp_path)
    source.fetch(2026)
    meta_snapshot = next((tmp_path / "snapshots" / "yahoo").glob("*meta*.json"))
    good = meta_snapshot.read_text()

    routes = _routes()
    routes[f"/fantasy/v2/league/{LEAGUE_KEY}"] = bad_payload
    broken = _source(tmp_path, routes=routes)
    with pytest.raises(ValueError):
        broken.fetch(2026, refresh=True)
    assert meta_snapshot.read_text() == good


def test_replayed_sync_reports_snapshot_age_not_now(tmp_path):
    import os

    _source(tmp_path).fetch(2026)
    past = 1_700_000_000  # 2023-11-14T22:13:20Z
    for path in (tmp_path / "snapshots").rglob("*.json"):
        os.utime(path, (past, past))
    bundle = _source(tmp_path).fetch(2026)
    assert bundle.data["synced_at"] == "2023-11-14T22:13:20Z"


def test_no_credential_reaches_snapshots_or_logs(tmp_path, caplog):
    with caplog.at_level("DEBUG"):
        _source(tmp_path).fetch(2026)
    assert ACCESS_TOKEN not in caplog.text
    for path in (tmp_path / "snapshots").rglob("*.json"):
        assert ACCESS_TOKEN not in path.read_text()


def test_requests_carry_bearer_auth_and_json_format(tmp_path):
    requests = []
    _source(tmp_path, recorder=requests).fetch(2026)
    assert all(r.url.params.get("format") == "json" for r in requests)
    assert all(r.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}" for r in requests)
