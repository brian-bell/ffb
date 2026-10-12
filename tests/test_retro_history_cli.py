"""``ffb retro history`` reads league-scoped ledgers and actuals."""

from ffb import config
from ffb.cli import app
from ffb.predlog import build_prediction_ledger, prediction_ledger_key
from ffb.retro import actuals_snapshot_key, build_lineup_snapshot, lineup_history_key
from ffb.snapshot import SnapshotCache

from .cli_plain import PlainCliRunner

runner = PlainCliRunner()
STAMP = "2026-10-10T11:00:00Z"


def _env(tmp_path):
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }


def _player(native_id, name, position, *, points, source_points, selected="BN", team="KC"):
    return {
        "team_key": "t.1",
        "native_id": native_id,
        "native_player_key": f"p.{native_id}",
        "player_key": f"mfl:{native_id}",
        "projection_key": f"mfl:{native_id}",
        "matched": True,
        "name": name,
        "position": position,
        "team": team,
        "selected_position": selected,
        "eligible_positions": [position],
        "points": points,
        "n": 1,
        "source_points": source_points,
        "injury": {"status": "QUESTIONABLE"} if native_id == "q" else None,
    }


def _ledger(league_key, player):
    return build_prediction_ledger(
        season=2026,
        week=1,
        league_key=league_key,
        user_team_key="t.1",
        generated_at=STAMP,
        scoring_weights={"rec": 1.0},
        projection_sources={},
        players=[player],
    )


def _actuals(source, raw_league_key, native_id, points):
    return {
        "schema_version": 2,
        "source": source,
        "synced_at": "2026-10-14T12:00:00Z",
        "league": {
            "league_id": "1",
            "league_key": raw_league_key,
            "name": "Test",
            "season": 2026,
            "week": 1,
            "num_teams": 2,
        },
        "matchups": [
            {
                "matchup_id": "1",
                "week": 1,
                "teams": [
                    {"team_key": "t.1", "points": points},
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
                "selected_position": "QB" if native_id == "sleeper-qb" else "RB",
                "points": points,
            }
        ],
    }


def _advice(native_id, name, position, points):
    row = {
        "native_id": native_id,
        "name": name,
        "position": position,
        "team": "KC",
        "selected_position": position,
        "eligible_positions": [position],
        "projection_key": f"mfl:{native_id}",
        "player_key": f"mfl:{native_id}",
        "points": points,
        "source_points": {},
        "n": 1,
        "matched": True,
    }
    ident = {
        "native_id": native_id,
        "name": name,
        "slot": position,
        "selected_position": position,
        "points": points,
    }
    return build_lineup_snapshot(
        season=2026,
        week=1,
        generated_at=STAMP,
        team_key="t.1",
        team_name="Brian",
        roster_slots={position: 1, "BN": 1},
        players=[row],
        report={
            "current": [ident],
            "optimal": [ident],
            "start": [],
            "sit": [],
            "current_total": points,
            "optimal_total": points,
            "delta": 0.0,
        },
    )


def _write_league(cache, league_key, *, source, raw_league_key, native_id, position, points):
    player = _player(
        native_id,
        native_id,
        position,
        points=points,
        source_points={"sleeper": points} if source == "sleeper" else {"espn": points},
        selected=position,
    )
    cache.put_json(
        prediction_ledger_key(2026, 1, STAMP, league_key),
        _ledger(league_key, player),
        mode=0o600,
    )
    cache.put_json(
        actuals_snapshot_key(2026, 1, league_key),
        _actuals(source, raw_league_key, native_id, points),
        mode=0o600,
    )
    cache.put_json(
        lineup_history_key(2026, 1, STAMP, league_key),
        _advice(native_id, native_id, position, points),
        mode=0o600,
    )


def test_history_is_scoped_to_each_leagues_snapshots(tmp_path):
    cache = SnapshotCache(tmp_path / "snapshots")
    _write_league(
        cache,
        config.YAHOO_LEAGUE_KEY,
        source="yahoo",
        raw_league_key="470.l.928421",
        native_id="yahoo-rb",
        position="RB",
        points=10.0,
    )
    _write_league(
        cache,
        config.SLEEPER_LEAGUE_KEY,
        source="sleeper",
        raw_league_key=config.SLEEPER_LEAGUE_ID,
        native_id="sleeper-qb",
        position="QB",
        points=4.0,
    )
    env = _env(tmp_path)

    yahoo = runner.invoke(
        app, ["retro", "history", "2026", "--league", config.YAHOO_LEAGUE_KEY], env=env
    )
    assert yahoo.exit_code == 0, yahoo.output
    assert "Source × position" in yahoo.output
    assert "RB" in yahoo.output
    assert "QB" not in yahoo.output
    assert "Questionable starts" in yahoo.output
    assert "Regret" in yahoo.output
    assert config.YAHOO_LEAGUE_KEY in yahoo.output
    assert config.SLEEPER_LEAGUE_KEY not in yahoo.output

    sleeper = runner.invoke(app, ["retro", "history", "2026", "--league", "sleeper"], env=env)
    assert sleeper.exit_code == 0, sleeper.output
    assert "QB" in sleeper.output
    assert "RB" not in sleeper.output
    assert config.SLEEPER_LEAGUE_KEY in sleeper.output


def test_weekly_retro_command_still_accepts_its_options():
    help_text = runner.invoke(app, ["retro", "--help"])
    assert help_text.exit_code == 0, help_text.output
    assert "--fixture" in help_text.output
    assert "--from-matchups" in help_text.output

    history_help = runner.invoke(app, ["retro", "history", "--help"])
    assert history_help.exit_code == 0, history_help.output
    assert "--league" in history_help.output
