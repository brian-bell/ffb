"""``league sync --league sleeper --push``: POST the live bundle it just imported."""

import json
from pathlib import Path

import httpx
import pytest

from ffb import cli, config
from ffb.cli import app
from ffb.snapshot import SnapshotCache
from ffb.sources import sleeper_league

from .cli_plain import PlainCliRunner
from .test_sleeper_league_source import _transport
from .test_sleeper_lineup_cli import _seed_store

runner = PlainCliRunner()

PUSH = ["league", "sync", "2026", "--league", "sleeper", "--push"]
TRACKER_ENV = {"FFB_TRACKER_URL": "https://tracker.test", "FFB_TRACKER_API_KEY": "sekrit"}


class _RecordingSource(sleeper_league.SleeperLeagueSource):
    """The live source over fixture routes, remembering the bundle it returned."""

    fetched: list = []

    def fetch(self, season, **kwargs):
        bundle = super().fetch(season, **kwargs)
        _RecordingSource.fetched.append(bundle)
        return bundle


@pytest.fixture
def live_sleeper(tmp_path, monkeypatch):
    env = _seed_store(tmp_path)
    _RecordingSource.fetched = []

    def build(cache: SnapshotCache):
        return _RecordingSource(
            league_id=config.SLEEPER_LEAGUE_ID,
            user_id=config.SLEEPER_USER_ID,
            cache=cache,
            transport=_transport(),
        )

    monkeypatch.setattr(sleeper_league, "league_source_from_env", build)
    return env


def _tracker(monkeypatch, response: httpx.Response) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    monkeypatch.setattr(
        cli, "_tracker_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return seen


def test_push_posts_the_synced_bundle_to_its_league_slot(live_sleeper, monkeypatch):
    seen = _tracker(
        monkeypatch,
        httpx.Response(
            200,
            json={
                "ok": True,
                "season": 2026,
                "current_week": 2,
                "teams": 12,
                "players": 180,
                "source": "sleeper",
                "synced_at": "2026-09-27T11:00:00Z",
            },
        ),
    )
    result = runner.invoke(app, PUSH, env={**live_sleeper, **TRACKER_ENV})
    assert result.exit_code == 0, result.output
    assert "Synced live Sleeper league state" in result.output
    assert "Published league bundle" in result.output
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/api/league/bundle"
    assert request.url.params["league"] == config.SLEEPER_LEAGUE_KEY
    assert json.loads(request.content) == _RecordingSource.fetched[0].data
    assert "sekrit" not in result.output


def test_push_treats_a_stale_bundle_conflict_as_success(live_sleeper, monkeypatch):
    _tracker(
        monkeypatch,
        httpx.Response(
            409,
            json={
                "error": "stale_bundle",
                "message": "bundle.synced_at a is older than stored b",
            },
        ),
    )
    result = runner.invoke(app, PUSH, env={**live_sleeper, **TRACKER_ENV})
    assert result.exit_code == 0, result.output
    assert "newer" in result.output


def test_push_reports_a_rejection_after_the_local_import(live_sleeper, monkeypatch):
    _tracker(monkeypatch, httpx.Response(401, json={"error": "unauthorized"}))
    result = runner.invoke(app, PUSH, env={**live_sleeper, **TRACKER_ENV})
    assert result.exit_code == 1
    assert "Synced live Sleeper league state" in result.output
    assert "unauthorized" in result.output
    assert "sekrit" not in result.output


def test_push_needs_the_tracker_configured_before_it_fetches(live_sleeper):
    result = runner.invoke(app, PUSH, env=live_sleeper)
    assert result.exit_code == 2
    assert "FFB_TRACKER_URL" in result.output
    assert _RecordingSource.fetched == []


@pytest.mark.parametrize(
    "extra",
    [
        ["--offline"],
        ["--week", "2"],
    ],
)
def test_push_applies_only_to_a_live_current_week_sync(live_sleeper, extra):
    result = runner.invoke(app, [*PUSH, *extra], env={**live_sleeper, **TRACKER_ENV})
    assert result.exit_code != 0
    assert "--push" in result.output
    assert _RecordingSource.fetched == []


@pytest.mark.parametrize(
    "args",
    [
        ["league", "sync", "2026", "--push"],
        ["league", "sync", "2026", "--from-tracker", "--push"],
        [
            "league",
            "sync",
            "2024",
            "--fixture",
            str(Path(__file__).parent / "fixtures" / "yahoo_league_minimal.json"),
            "--push",
        ],
    ],
)
def test_push_is_rejected_outside_a_live_sleeper_sync(live_sleeper, args):
    result = runner.invoke(app, args, env={**live_sleeper, **TRACKER_ENV})
    assert result.exit_code != 0
    assert "--push" in result.output
    assert "Traceback" not in result.output
