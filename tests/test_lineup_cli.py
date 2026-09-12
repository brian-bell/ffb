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


def test_lineup_reresolves_roster_after_late_crosswalk(tmp_path):
    env = _env(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.close()
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert synced.exit_code == 0, synced.output

    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    from ffb.sources.crosswalk import parse_crosswalk

    store.upsert_crosswalk(
        parse_crosswalk(json.loads(XWALK.read_text()))
        + [
            {
                "player_key": "rb-low",
                "sleeper_id": None,
                "espn_id": None,
                "yahoo_id": "55501",
                "gsis_id": None,
                "full_name": "Slow Back",
                "position": "RB",
                "team": "KCC",
            }
        ]
    )
    henry = next(row for row in store.league_roster_rows(2024) if row["yahoo_player_id"] == "29279")
    assert henry["matched"] is False
    assert henry["player_key"] == "yahoo:29279"
    store.upsert_projections(
        [
            _weekly_row("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 180.0}),
            _weekly_row("rb-low", "Slow Back", "RB", "KCC", "slow", {"rush_yd": 40.0}),
        ]
    )
    store.close()

    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "Derrick Henry" in result.output
    assert "18.0" in " ".join(result.output.split())
    assert "Start" in result.output


def test_lineup_table_keeps_vacant_optimal_under_the_same_slot(tmp_path):
    env = _env(tmp_path)
    payload = json.loads(FIXTURE.read_text())
    payload["settings"]["roster_slots"] = [
        {"position": "QB", "count": 1, "is_starting": True},
        {"position": "RB", "count": 1, "is_starting": True},
        {"position": "BN", "count": 1, "is_starting": False},
    ]
    payload["rosters"][0]["players"] = [
        {
            "yahoo_player_id": "42025",
            "yahoo_player_key": "1.p.42025",
            "name": "Rookie QB",
            "nfl_team": "CHI",
            "primary_position": "QB",
            "eligible_positions": ["QB"],
            "selected_position": "QB",
        },
        {
            "yahoo_player_id": "29279",
            "yahoo_player_key": "1.p.29279",
            "name": "Derrick Henry",
            "nfl_team": "BAL",
            "primary_position": "RB",
            "eligible_positions": ["RB"],
            "selected_position": "RB",
        },
    ]
    fixture_path = tmp_path / "vacant.json"
    fixture_path.write_text(json.dumps(payload))
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    from ffb.sources.crosswalk import parse_crosswalk

    store.upsert_crosswalk(parse_crosswalk(json.loads(XWALK.read_text())))
    store.upsert_projections(
        [_weekly_row("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 180.0})]
    )
    store.close()
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(fixture_path)], env=env)
    assert synced.exit_code == 0, synced.output
    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "Sit" not in result.output
    assert "Rookie QB" in result.output
    qb_line = next(line for line in result.output.splitlines() if "QB" in line and "Rookie" in line)
    assert "Derrick Henry" not in qb_line
