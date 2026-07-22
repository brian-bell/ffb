"""Fixture-backed league sync/show behavior."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_league_minimal.json"
XWALK_FIXTURE = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"


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


def test_league_sync_loads_crosswalk_before_resolving_rosters(tmp_path):
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

    result = runner.invoke(
        app,
        ["league", "sync", "--season", "2024", "--fixture", str(fixture_path)],
        env={"FFB_DB_PATH": str(tmp_path / "ffb.duckdb"), "FFB_SNAPSHOT_DIR": str(snapshots)},
    )

    assert result.exit_code == 0, result.output
    assert "1 matched, 0 unmatched" in " ".join(result.output.split())
