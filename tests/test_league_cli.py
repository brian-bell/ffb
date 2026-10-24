"""Fixture-backed league sync/show behavior."""

import json
from pathlib import Path

from ffb.cli import app
from ffb.store import Store

from .cli_plain import PlainCliRunner

runner = PlainCliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_league_minimal.json"
XWALK_FIXTURE = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"


def test_two_leagues_persist_and_read_back_independently_in_one_season(tmp_path):
    """league_* is keyed by (season, league_key): one sync must not clobber the other."""
    db = tmp_path / "ffb.duckdb"
    env = {"FFB_DB_PATH": str(db)}
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )

    sleeper = json.loads(FIXTURE.read_text()) | {"source": "sleeper"}
    sleeper["league"] = sleeper["league"] | {"league_key": "999", "name": "Sleeper League"}
    path = tmp_path / "sleeper_bundle.json"
    path.write_text(json.dumps(sleeper))
    result = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(path)], env=env)
    assert result.exit_code == 0, result.output

    store = Store(db)
    try:
        assert store.league_keys(2024) == ["sleeper:999", "yahoo:1.l.mock-1"]
        yahoo_state = store.league_context(2024, "yahoo:1.l.mock-1")
        sleeper_state = store.league_context(2024, "sleeper:999")
        assert yahoo_state["name"] == "Mock League"
        assert sleeper_state["name"] == "Sleeper League"
        # Each league keeps its own provider source and raw provider key.
        assert sleeper_state["source"] == "sleeper"
        assert sleeper_state["provider_league_key"] == "999"
        assert yahoo_state["source"] == "fixture"
    finally:
        store.close()


def test_league_sync_then_show_displays_persisted_fixture_state(tmp_path):
    env = {"FFB_DB_PATH": str(tmp_path / "ffb.duckdb")}
    sync = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert sync.exit_code == 0, sync.output

    shown = runner.invoke(app, ["league", "show", "2024"], env=env)
    assert shown.exit_code == 0, shown.output
    assert "Mock fixture settings" in shown.output
    for value in ("Mock League", "Passing Yards", "QB", "Brian's Team", "0"):
        assert value in shown.output


def test_league_sync_without_fixture_or_tracker_warns_instead_of_syncing_live_yahoo(tmp_path):
    result = runner.invoke(
        app,
        ["league", "sync", "2026", "--league", "yahoo"],
        env={"FFB_DB_PATH": str(tmp_path / "db")},
    )
    assert result.exit_code == 0
    assert "No live Yahoo sync" in result.output


def test_league_sync_uses_explicitly_synced_crosswalk_to_resolve_rosters(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    fixture["rosters"][0]["players"] = [
        {
            "native_id": "29279",
            "native_player_key": "1.p.29279",
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
