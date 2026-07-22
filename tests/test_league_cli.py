"""Fixture-backed league sync/show behavior."""

from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_league_minimal.json"


def test_league_sync_then_show_displays_persisted_fixture_state(tmp_path):
    env = {"FFB_DB_PATH": str(tmp_path / "ffb.duckdb")}
    sync = runner.invoke(
        app, ["league", "sync", "--season", "2024", "--fixture", str(FIXTURE)], env=env
    )
    assert sync.exit_code == 0, sync.output

    shown = runner.invoke(app, ["league", "show", "--season", "2024"], env=env)
    assert shown.exit_code == 0, shown.output
    for value in ("Mock League", "Passing Yards", "QB", "Brian's Team", "0"):
        assert value in shown.output


def test_league_sync_without_fixture_explains_live_yahoo_is_pending(tmp_path):
    result = runner.invoke(app, ["league", "sync"], env={"FFB_DB_PATH": str(tmp_path / "db")})
    assert result.exit_code == 2
    assert "Task 2b" in result.output
