"""Fixture-backed league sync/show behavior."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app
from ffb.league import parse_bundle
from ffb.sources import yahoo
from ffb.yahoo_auth import YahooAuthError

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_league_minimal.json"
XWALK_FIXTURE = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"


def test_league_sync_then_show_displays_persisted_fixture_state(tmp_path):
    env = {"FFB_DB_PATH": str(tmp_path / "ffb.duckdb")}
    sync = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert sync.exit_code == 0, sync.output

    shown = runner.invoke(app, ["league", "show", "2024"], env=env)
    assert shown.exit_code == 0, shown.output
    for value in ("Mock League", "Passing Yards", "QB", "Brian's Team", "0"):
        assert value in shown.output


class _StubLeagueSource:
    """Records fetch calls and returns a validated yahoo-sourced bundle."""

    def __init__(self):
        self.calls = []

    def fetch(self, season, *, refresh=False):
        self.calls.append({"season": season, "refresh": refresh})
        payload = json.loads(FIXTURE.read_text())
        payload["source"] = "yahoo"
        return parse_bundle(payload, season=season)


def test_league_sync_without_fixture_uses_the_live_yahoo_source(tmp_path, monkeypatch):
    stub = _StubLeagueSource()
    monkeypatch.setattr(yahoo, "league_source_from_env", lambda cache: stub)
    env = {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }
    result = runner.invoke(app, ["league", "sync", "2024", "--refresh"], env=env)
    assert result.exit_code == 0, result.output
    assert stub.calls == [{"season": 2024, "refresh": True}]
    assert "live Yahoo league state" in result.output

    shown = runner.invoke(app, ["league", "show", "2024"], env=env)
    assert shown.exit_code == 0, shown.output
    assert "Mock League" in shown.output


def test_league_sync_without_fixture_reports_missing_configuration(tmp_path, monkeypatch):
    for var in (
        "FFB_YAHOO_LEAGUE_KEY",
        "FFB_YAHOO_CLIENT_ID",
        "FFB_YAHOO_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    result = runner.invoke(app, ["league", "sync"], env={"FFB_DB_PATH": str(tmp_path / "db")})
    assert result.exit_code == 2
    assert "FFB_YAHOO_LEAGUE_KEY" in result.output


def test_league_sync_auth_failure_reports_without_leaking_secrets(tmp_path, monkeypatch):
    def raise_auth(cache):
        raise YahooAuthError("no stored Yahoo token; complete the one-time browser authorization")

    monkeypatch.setattr(yahoo, "league_source_from_env", raise_auth)
    result = runner.invoke(app, ["league", "sync"], env={"FFB_DB_PATH": str(tmp_path / "db")})
    assert result.exit_code == 2
    assert "authorization" in result.output


def test_snapshotted_league_replays_without_oauth_credentials(tmp_path, monkeypatch):
    """The documented credential-free cached replay works through the factory."""
    from ffb.snapshot import SnapshotCache
    from ffb.sources.yahoo import YahooLeagueSource
    from tests.test_yahoo_source import ACCESS_TOKEN, LEAGUE_KEY, _transport

    snapshot_dir = tmp_path / "snapshots"
    YahooLeagueSource(
        league_key=LEAGUE_KEY,
        cache=SnapshotCache(snapshot_dir),
        token_provider=lambda: ACCESS_TOKEN,
        transport=_transport(),
    ).fetch(2026)

    for var in ("FFB_YAHOO_CLIENT_ID", "FFB_YAHOO_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    env = {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(snapshot_dir),
        "FFB_YAHOO_LEAGUE_KEY": LEAGUE_KEY,
    }
    result = runner.invoke(app, ["league", "sync", "2026"], env=env)
    assert result.exit_code == 0, result.output
    assert "live Yahoo league state" in result.output


def test_live_sync_keeps_credentials_out_of_db_snapshots_and_output(tmp_path, monkeypatch):
    """End-to-end hygiene: token material never lands in any persisted byte."""

    from tests.test_yahoo_source import ACCESS_TOKEN, LEAGUE_KEY, _transport

    def build(cache):
        return yahoo.YahooLeagueSource(
            league_key=LEAGUE_KEY,
            cache=cache,
            token_provider=lambda: ACCESS_TOKEN,
            transport=_transport(),
        )

    monkeypatch.setattr(yahoo, "league_source_from_env", build)
    db_path = tmp_path / "ffb.duckdb"
    snapshot_dir = tmp_path / "snapshots"
    env = {"FFB_DB_PATH": str(db_path), "FFB_SNAPSHOT_DIR": str(snapshot_dir)}
    result = runner.invoke(app, ["league", "sync", "2026"], env=env)
    assert result.exit_code == 0, result.output
    assert ACCESS_TOKEN not in result.output
    assert ACCESS_TOKEN.encode() not in db_path.read_bytes()
    for path in snapshot_dir.rglob("*.json"):
        assert ACCESS_TOKEN not in path.read_text()


def test_league_sync_uses_explicitly_synced_crosswalk_to_resolve_rosters(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    fixture["rosters"][0]["players"] = [
        {
            "yahoo_player_id": "29279",
            "yahoo_player_key": "1.p.29279",
            "name": "Derrick Henry",
            "nfl_team": "BAL",
            "primary_position": "RB",
            "eligible_positions": ["RB"],
            "selected_position": "RB",
        }
    ]
    fixture_path = tmp_path / "league.json"
    fixture_path.write_text(json.dumps(fixture))
    snapshots = tmp_path / "snapshots"
    xwalk_snapshot = snapshots / "nflverse" / "ff_playerids.json"
    xwalk_snapshot.parent.mkdir(parents=True)
    xwalk_snapshot.write_text(XWALK_FIXTURE.read_text())

    env = {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(snapshots),
    }
    season_sync = runner.invoke(
        app,
        ["season", "sync", "2024", "--offline", "--source", "sleeper"],
        env=env,
    )
    assert "crosswalk" in season_sync.output

    result = runner.invoke(
        app,
        ["league", "sync", "2024", "--fixture", str(fixture_path)],
        env=env,
    )

    assert result.exit_code == 0, result.output
    assert "1 matched, 0 unmatched" in " ".join(result.output.split())

    shown = runner.invoke(
        app,
        ["league", "show", "2024", "--rosters"],
        env={"FFB_DB_PATH": str(tmp_path / "ffb.duckdb")},
    )
    assert shown.exit_code == 0, shown.output
    assert "Derrick Henry (RB)" in shown.output
