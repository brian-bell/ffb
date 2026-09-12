"""CLI sit/start report against a synthetic league fixture."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app
from ffb.store import Store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_lineup_sitstart.json"
XWALK = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"


def _env(tmp_path):
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }


def _weekly_row(player_key, name, position, team, native_id, stats, matched=True):
    return {
        "player_key": player_key,
        "native_id": native_id,
        "full_name": name,
        "position": position,
        "team": team,
        "matched": matched,
        "season": 2024,
        "source": "sleeper",
        "scope": "week1",
        "stats": stats,
        "src_pts_ppr": None,
        "draftable": True,
    }


def _seed_lineup_store(tmp_path):
    from ffb.sources.crosswalk import parse_crosswalk

    env = _env(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    extra = [
        {
            "player_key": "rb-low",
            "sleeper_id": None,
            "espn_id": None,
            "yahoo_id": "55501",
            "gsis_id": None,
            "full_name": "Slow Back",
            "position": "RB",
            "team": "KCC",
        },
        {
            "player_key": "wr-flex",
            "sleeper_id": None,
            "espn_id": None,
            "yahoo_id": "55502",
            "gsis_id": None,
            "full_name": "Flex Filler",
            "position": "WR",
            "team": "CHI",
        },
    ]
    store.upsert_crosswalk(parse_crosswalk(json.loads(XWALK.read_text())) + extra)
    store.upsert_projections(
        [
            _weekly_row("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 180.0}),
            _weekly_row("rb-low", "Slow Back", "RB", "KCC", "slow", {"rush_yd": 40.0}),
            _weekly_row(
                "13971",
                "Ja'Marr Chase",
                "WR",
                "CIN",
                "7564",
                {"rec": 10.0, "rec_yd": 80.0},
            ),
            _weekly_row(
                "wr-flex",
                "Flex Filler",
                "WR",
                "CHI",
                "flex",
                {"rec": 2.0, "rec_yd": 20.0},
            ),
            _weekly_row("def:SFO", "49ers", "DEF", "SFO", "SF", {"sack": 3.0}),
        ]
    )
    store.close()
    return env


def test_lineup_recommends_bench_rb_over_current_starter(tmp_path):
    env = _seed_lineup_store(tmp_path)
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert synced.exit_code == 0, synced.output

    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Week 1" in result.output
    assert "Brian's Team" in result.output
    assert "Derrick Henry" in result.output
    assert "Slow Back" in result.output
    assert "Start" in result.output
    assert "Sit" in result.output
    assert "18.0" in output
    assert "4.0" in output


def test_lineup_defaults_to_stored_current_week(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "Week 1" in result.output


def test_lineup_requires_league_state(tmp_path):
    env = _env(tmp_path)
    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 1
    assert "league sync" in result.output


def test_lineup_requires_a_user_team(tmp_path):
    env = _seed_lineup_store(tmp_path)
    payload = json.loads(FIXTURE.read_text())
    payload["teams"][0]["is_user_team"] = False
    path = tmp_path / "no-user.json"
    path.write_text(json.dumps(payload))
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(path)], env=env).exit_code
        == 0
    )
    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 1
    assert "is_user_team" in result.output


def test_lineup_requires_weekly_projections(tmp_path):
    env = _env(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.close()
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 1
    assert "--week" in result.output


def test_lineup_rejects_non_positive_week(tmp_path):
    result = runner.invoke(app, ["lineup", "2024", "--week", "0"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "week" in result.output.lower()
