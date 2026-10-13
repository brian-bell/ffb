"""``ffb calibrate backtest`` reads both league snapshot trees and only prints the gate."""

from io import StringIO

from rich.console import Console

from ffb import config
from ffb.cli import _render_shadow, app
from ffb.predlog import build_prediction_ledger, prediction_ledger_key
from ffb.retro import actuals_snapshot_key, build_lineup_snapshot, lineup_history_key
from ffb.snapshot import SnapshotCache

from .cli_plain import PlainCliRunner

runner = PlainCliRunner()
STAMP = "2026-10-10T11:00:00Z"


def test_backtest_reads_flat_yahoo_and_nested_sleeper_without_writing(tmp_path):
    cache = SnapshotCache(tmp_path / "snapshots")
    for week, actual in ((1, 10.0), (3, 10.0), (4, 80.0)):
        _write_week(cache, config.YAHOO_LEAGUE_KEY, "yahoo", "470.l.928421", week, actual)
        _write_week(
            cache,
            config.SLEEPER_LEAGUE_KEY,
            "sleeper",
            config.SLEEPER_LEAGUE_ID,
            week,
            actual,
        )
    yahoo_key = prediction_ledger_key(2026, 3, STAMP, config.YAHOO_LEAGUE_KEY)
    sleeper_key = prediction_ledger_key(2026, 3, STAMP, config.SLEEPER_LEAGUE_KEY)
    assert yahoo_key == f"predlog/2026_week3/{STAMP.replace(':', '-')}"
    assert sleeper_key.startswith("predlog/sleeper_1395854363380965376/2026_week3/")
    before = _files(tmp_path)
    env = _env(tmp_path)

    pooled = runner.invoke(app, ["calibrate", "backtest", "2026"], env=env)

    assert pooled.exit_code == 0, pooled.output
    assert _files(tmp_path) == before
    assert "GATE FAIL" in pooled.output
    assert "fewer than 6 backtest weeks" in pooled.output
    assert config.YAHOO_LEAGUE_KEY in pooled.output
    assert config.SLEEPER_LEAGUE_KEY in pooled.output
    assert _fit_token(pooled.output, 3) == "1"
    assert _fit_token(pooled.output, 4) == "1,3"
    assert "Published" not in pooled.output

    sleeper = runner.invoke(app, ["calibrate", "backtest", "2026", "--league", "sleeper"], env=env)
    assert sleeper.exit_code == 0, sleeper.output
    assert "per-league" in sleeper.output
    assert config.SLEEPER_LEAGUE_KEY in sleeper.output
    assert config.YAHOO_LEAGUE_KEY not in sleeper.output
    assert "per-league backtest is not the promotion gate" in sleeper.output
    assert _files(tmp_path) == before


def test_empty_backtest_fails_closed(tmp_path):
    result = runner.invoke(app, ["calibrate", "backtest", "2026"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert "No scored weeks." in result.output
    assert "GATE FAIL" in result.output
    assert "fewer than 6 backtest weeks" in result.output
    assert not (tmp_path / "snapshots").exists()


def test_lineup_help_names_the_model_switch():
    result = runner.invoke(app, ["lineup", "--help"])

    assert result.exit_code == 0, result.output
    assert "--model" in result.output
    assert "calibrated" in result.output


def test_backtest_help_names_the_league_variant():
    result = runner.invoke(app, ["calibrate", "backtest", "--help"])

    assert result.exit_code == 0, result.output
    assert "--league" in result.output


def test_shadow_render_prints_both_close_call_rules(monkeypatch):
    import ffb.cli as cli_mod

    buffer = StringIO()
    monkeypatch.setattr(cli_mod, "console", Console(file=buffer, force_terminal=False, width=120))
    _render_shadow(
        {
            "warning": None,
            "model": "calibrated",
            "recommended_total": 10,
            "started_total": 8,
            "delta": 2,
            "matches_advice": True,
            "start": [],
            "sit": [],
            "close_calls": [{"name": "Bravo", "versus": "Alpha", "slot": "RB", "delta": 1.0}],
            "probability_close_calls": [
                {
                    "name": "Charlie",
                    "versus": "Alpha",
                    "slot": "RB",
                    "delta": 3.0,
                    "p_win": 0.55,
                }
            ],
        }
    )

    text = buffer.getvalue()
    assert "Shadow matches the advice snapshot." in text
    assert "Close call (1.5) Bravo vs Alpha RB (1.0)" in text
    assert "Close call (p) Charlie vs Alpha RB (55% to be right, gap 3.0)" in text


def _env(tmp_path):
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }


def _files(tmp_path):
    root = tmp_path / "snapshots"
    if not root.exists():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _fit_token(output, week):
    line = next(line for line in output.splitlines() if line.startswith(f"Week {week} "))
    return line.split("fit=", 1)[1].split()[0]


def _write_week(cache, league_key, source, raw_league_key, week, actual):
    native_id = f"{source}-{week}"
    player = {
        "team_key": "t.1",
        "native_id": native_id,
        "native_player_key": f"p.{native_id}",
        "player_key": f"mfl:{native_id}",
        "projection_key": f"mfl:{native_id}",
        "matched": True,
        "name": native_id,
        "position": "RB",
        "team": "KC",
        "selected_position": "RB",
        "eligible_positions": ["RB"],
        "points": 10.0,
        "n": 1,
        "source_points": {"sleeper": 10.0, "espn": 10.0},
    }
    cache.put_json(
        prediction_ledger_key(2026, week, STAMP, league_key),
        build_prediction_ledger(
            season=2026,
            week=week,
            league_key=league_key,
            user_team_key="t.1",
            generated_at=STAMP,
            scoring_weights={"rec": 1.0},
            projection_sources={},
            players=[player],
        ),
        mode=0o600,
    )
    cache.put_json(
        actuals_snapshot_key(2026, week, league_key),
        {
            "schema_version": 2,
            "source": source,
            "synced_at": "2026-10-14T12:00:00Z",
            "league": {
                "league_id": "1",
                "league_key": raw_league_key,
                "name": "Test",
                "season": 2026,
                "week": week,
                "num_teams": 2,
            },
            "matchups": [
                {
                    "matchup_id": "1",
                    "week": week,
                    "teams": [
                        {"team_key": "t.1", "points": actual},
                        {"team_key": "t.2", "points": 0.0},
                    ],
                }
            ],
            "players": [
                {
                    "native_id": native_id,
                    "native_player_key": f"p.{native_id}",
                    "name": native_id,
                    "team_key": "t.1",
                    "selected_position": "RB",
                    "points": actual,
                }
            ],
        },
        mode=0o600,
    )
    ident = {
        "native_id": native_id,
        "name": native_id,
        "slot": "RB",
        "selected_position": "RB",
        "points": 10.0,
    }
    cache.put_json(
        lineup_history_key(2026, week, STAMP, league_key),
        build_lineup_snapshot(
            season=2026,
            week=week,
            generated_at=STAMP,
            team_key="t.1",
            team_name="Brian",
            roster_slots={"RB": 1, "BN": 1},
            players=[
                {
                    "native_id": native_id,
                    "native_player_key": f"p.{native_id}",
                    "name": native_id,
                    "position": "RB",
                    "team": "KC",
                    "selected_position": "RB",
                    "eligible_positions": ["RB"],
                    "projection_key": f"mfl:{native_id}",
                    "player_key": f"mfl:{native_id}",
                    "points": 10.0,
                    "source_points": {"sleeper": 10.0},
                    "n": 1,
                    "matched": True,
                }
            ],
            report={
                "current": [ident],
                "optimal": [ident],
                "start": [],
                "sit": [],
                "current_total": 10.0,
                "optimal_total": 10.0,
                "delta": 0.0,
            },
        ),
        mode=0o600,
    )
