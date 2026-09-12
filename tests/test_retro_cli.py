"""CLI weekly retro against synthetic lineup + actuals fixtures."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app
from ffb.retro import actuals_snapshot_key, lineup_snapshot_key
from ffb.snapshot import SnapshotCache
from ffb.store import Store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_lineup_sitstart.json"
ACTUALS = Path(__file__).parent / "fixtures" / "weekly_actuals_minimal.json"
XWALK = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"


def _env(tmp_path):
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }


def _weekly_row(player_key, name, position, team, native_id, stats, scope="week1"):
    return {
        "player_key": player_key,
        "native_id": native_id,
        "full_name": name,
        "position": position,
        "team": team,
        "matched": True,
        "season": 2024,
        "source": "sleeper",
        "scope": scope,
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


def test_lineup_writes_a_recommendation_snapshot(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 0, result.output
    cache = SnapshotCache(tmp_path / "snapshots")
    snapshot = cache.read_json(lineup_snapshot_key(2024, 1))
    assert snapshot["kind"] == "lineup_recommendation"
    assert snapshot["week"] == 1
    assert any(row["yahoo_player_id"] == "29279" for row in snapshot["players"])
    assert snapshot["report"]["start"][0]["name"] == "Derrick Henry"


def test_retro_compares_advice_to_actuals_fixture(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    assert runner.invoke(app, ["lineup", "2024"], env=env).exit_code == 0

    result = runner.invoke(app, ["retro", "2024", "--fixture", str(ACTUALS)], env=env)
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Week 1 retro" in result.output
    assert "Brian's Team" in result.output
    assert "Recommended 46.5" in output
    assert "Started 25.5" in output
    assert "Δ +21.0" in output
    assert "Derrick Henry" in result.output
    assert "Flex Filler" in result.output
    assert "Start miss" in result.output
    assert "Sit miss" in result.output
    assert "sleeper" in result.output
    cache = SnapshotCache(tmp_path / "snapshots")
    stored = cache.read_json(actuals_snapshot_key(2024, 1))
    assert stored["league"]["week"] == 1


def test_retro_reads_stored_actuals_snapshot_without_fixture_flag(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    assert runner.invoke(app, ["lineup", "2024"], env=env).exit_code == 0
    first = runner.invoke(app, ["retro", "2024", "--fixture", str(ACTUALS)], env=env)
    assert first.exit_code == 0, first.output
    replay = runner.invoke(app, ["retro", "2024"], env=env)
    assert replay.exit_code == 0, replay.output
    assert "Recommended 46.5" in " ".join(replay.output.split())


def test_retro_requires_lineup_snapshot(tmp_path):
    env = _env(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.close()
    result = runner.invoke(app, ["retro", "2024", "--fixture", str(ACTUALS)], env=env)
    assert result.exit_code == 1
    assert "lineup" in result.output


def test_retro_requires_actuals_bundle(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    assert runner.invoke(app, ["lineup", "2024"], env=env).exit_code == 0
    result = runner.invoke(app, ["retro", "2024"], env=env)
    assert result.exit_code == 1
    assert "/api/actuals" in result.output
    assert "--fixture" in result.output


def test_retro_rejects_non_positive_week(tmp_path):
    result = runner.invoke(app, ["retro", "2024", "--week", "0"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "week" in result.output.lower()
