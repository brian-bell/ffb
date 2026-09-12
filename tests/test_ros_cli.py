"""CLI rest-of-season report against synthetic projections and schedule games."""

from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app
from ffb.store import Store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_lineup_sitstart.json"


def _env(tmp_path):
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }


def _season_row(player_key, name, position, team, native_id, stats):
    return {
        "player_key": player_key,
        "native_id": native_id,
        "full_name": name,
        "position": position,
        "team": team,
        "matched": True,
        "season": 2024,
        "source": "sleeper",
        "scope": "season",
        "stats": stats,
        "src_pts_ppr": None,
        "draftable": True,
    }


def _game(week, home, away):
    return {
        "season": 2024,
        "source": "schedule",
        "week": week,
        "home_team": home,
        "away_team": away,
    }


def _seed_ros_store(tmp_path):
    env = _env(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.upsert_projections(
        [
            _season_row("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 180.0}),
            _season_row(
                "13971",
                "Ja'Marr Chase",
                "WR",
                "CIN",
                "7564",
                {"rec": 10.0, "rec_yd": 80.0},
            ),
            _season_row("def:BUF", "Bills", "DEF", "BUF", "BUF", {"sack": 12.0}),
            _season_row("def:CLE", "Browns", "DEF", "CLE", "CLE", {"sack": 3.0}),
        ]
    )
    store.replace_team_byes(
        [
            {"season": 2024, "source": "schedule", "team": "BAL", "bye": 14},
            {"season": 2024, "source": "schedule", "team": "CIN", "bye": 12},
            {"season": 2024, "source": "schedule", "team": "KCC", "bye": 5},
            {"season": 2024, "source": "schedule", "team": "CHI", "bye": 5},
            {"season": 2024, "source": "schedule", "team": "SFO", "bye": 9},
        ],
        2024,
    )
    store.replace_schedule_games(
        [
            _game(15, "BAL", "BUF"),
            _game(16, "KCC", "BAL"),
            _game(15, "CLE", "CIN"),
            _game(16, "CIN", "PIT"),
        ],
        2024,
    )
    store.close()
    return env


def test_ros_prints_consensus_playoff_slate_and_bye_week(tmp_path):
    env = _seed_ros_store(tmp_path)
    result = runner.invoke(app, ["ros", "2024"], env=env)
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Rest-of-season" in result.output or "rest-of-season" in result.output.lower()
    assert "Derrick Henry" in result.output
    assert "Ja'Marr Chase" in result.output
    assert "18.0" in output
    assert "BUF" in result.output
    assert "@KCC" in result.output
    assert "14" in result.output
    assert "15" in result.output and "16" in result.output


def test_ros_filters_by_position(tmp_path):
    env = _seed_ros_store(tmp_path)
    result = runner.invoke(app, ["ros", "2024", "-p", "RB"], env=env)
    assert result.exit_code == 0, result.output
    assert "Derrick Henry" in result.output
    assert "Ja'Marr Chase" not in result.output


def test_ros_requires_season_projections(tmp_path):
    env = _env(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.close()
    result = runner.invoke(app, ["ros", "2024"], env=env)
    assert result.exit_code == 1
    assert "projection" in result.output.lower()


def test_ros_warns_when_schedule_games_are_missing(tmp_path):
    env = _env(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.upsert_projections(
        [_season_row("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 180.0})]
    )
    store.close()
    result = runner.invoke(app, ["ros", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "schedule" in result.output.lower()
    assert "Derrick Henry" in result.output


def test_ros_shows_roster_bye_plan_when_league_state_exists(tmp_path):
    env = _seed_ros_store(tmp_path)
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert synced.exit_code == 0, synced.output
    result = runner.invoke(app, ["ros", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "Bye" in result.output
    assert "Slow Back" in result.output or "Derrick Henry" in result.output


def test_ros_rejects_invalid_playoff_weeks(tmp_path):
    result = runner.invoke(app, ["ros", "2024", "--playoff-weeks", "15,0"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "playoff" in result.output.lower() or "week" in result.output.lower()


def test_ros_omits_usage_flags_when_usage_is_not_ingested(tmp_path):
    env = _seed_ros_store(tmp_path)
    result = runner.invoke(app, ["ros", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "usage" in result.output.lower()
