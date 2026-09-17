"""``ffb lineup --league sleeper`` is fixture-backed and does not write league_*."""

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from ffb import config
from ffb.cli import app
from ffb.retro import lineup_snapshot_key
from ffb.snapshot import SnapshotCache
from ffb.sources.crosswalk import parse_crosswalk
from ffb.store import Store

runner = CliRunner()


def _plain(text):
    """Strip rich's ANSI so assertions survive a color-capable terminal."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


FIXTURES = Path(__file__).parent / "fixtures" / "sleeper"
XWALK = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"
LEAGUE_ID = config.SLEEPER_LEAGUE_ID


def _env(tmp_path):
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
        "FFB_SLEEPER_LEAGUE_ID": LEAGUE_ID,
        "FFB_SLEEPER_USER_ID": config.SLEEPER_USER_ID,
    }


def _weekly_row(player_key, name, position, team, native_id, stats, matched=True, scope="week2"):
    return {
        "player_key": player_key,
        "native_id": native_id,
        "full_name": name,
        "position": position,
        "team": team,
        "matched": matched,
        "season": 2026,
        "source": "sleeper",
        "scope": scope,
        "stats": stats,
        "src_pts_ppr": None,
        "draftable": True,
    }


def _seed_snapshots(tmp_path):
    cache = SnapshotCache(tmp_path / "snapshots")
    mapping = {
        f"sleeper/league_{LEAGUE_ID}_league": "league.json",
        f"sleeper/league_{LEAGUE_ID}_rosters": "rosters.json",
        f"sleeper/league_{LEAGUE_ID}_users": "users.json",
        "sleeper/state_nfl": "state_nfl.json",
        "sleeper/players_nfl": "players.json",
    }
    for key, name in mapping.items():
        cache.put_json(key, json.loads((FIXTURES / name).read_text()))


def _seed_store(tmp_path):
    env = _env(tmp_path)
    _seed_snapshots(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    extras = [
        {
            "player_key": "rb-low",
            "sleeper_id": "slow",
            "espn_id": None,
            "yahoo_id": "55501",
            "gsis_id": None,
            "full_name": "Slow Back",
            "position": "RB",
            "team": "KCC",
        },
        {
            "player_key": "rb-ok",
            "sleeper_id": "rb-ok",
            "espn_id": None,
            "yahoo_id": None,
            "gsis_id": None,
            "full_name": "Okay Runner",
            "position": "RB",
            "team": "CHI",
        },
        {
            "player_key": "wr-flex",
            "sleeper_id": "flex",
            "espn_id": None,
            "yahoo_id": "55502",
            "gsis_id": None,
            "full_name": "Flex Filler",
            "position": "WR",
            "team": "CHI",
        },
        {
            "player_key": "wr-ok",
            "sleeper_id": "wr-ok",
            "espn_id": None,
            "yahoo_id": None,
            "gsis_id": None,
            "full_name": "Okay Receiver",
            "position": "WR",
            "team": "DAL",
        },
        {
            "player_key": "te-ok",
            "sleeper_id": "te-ok",
            "espn_id": None,
            "yahoo_id": None,
            "gsis_id": None,
            "full_name": "Okay Tightend",
            "position": "TE",
            "team": "DET",
        },
        {
            "player_key": "wr-deep",
            "sleeper_id": "flex2",
            "espn_id": None,
            "yahoo_id": None,
            "gsis_id": None,
            "full_name": "Deep Bench",
            "position": "WR",
            "team": "NYJ",
        },
        {
            "player_key": "qb-test",
            "sleeper_id": "qb-test",
            "espn_id": None,
            "yahoo_id": None,
            "gsis_id": None,
            "full_name": "Test Quarterback",
            "position": "QB",
            "team": "PIT",
        },
    ]
    store.upsert_crosswalk(parse_crosswalk(json.loads(XWALK.read_text())) + extras)
    store.upsert_projections(
        [
            _weekly_row("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 180.0}),
            _weekly_row("rb-low", "Slow Back", "RB", "KCC", "slow", {"rush_yd": 40.0}),
            _weekly_row("rb-ok", "Okay Runner", "RB", "CHI", "rb-ok", {"rush_yd": 50.0}),
            _weekly_row(
                "13971",
                "Ja'Marr Chase",
                "WR",
                "CIN",
                "7564",
                {"rec": 10.0, "rec_yd": 80.0},
            ),
            _weekly_row(
                "wr-flex", "Flex Filler", "WR", "CHI", "flex", {"rec": 2.0, "rec_yd": 20.0}
            ),
            _weekly_row(
                "wr-ok", "Okay Receiver", "WR", "DAL", "wr-ok", {"rec": 3.0, "rec_yd": 30.0}
            ),
            _weekly_row(
                "wr-deep", "Deep Bench", "WR", "NYJ", "flex2", {"rec": 1.0, "rec_yd": 10.0}
            ),
            _weekly_row(
                "te-ok", "Okay Tightend", "TE", "DET", "te-ok", {"rec": 2.0, "rec_yd": 20.0}
            ),
            _weekly_row("qb-test", "Test Quarterback", "QB", "PIT", "qb-test", {"pass_td": 1.0}),
            _weekly_row("10976", "Justin Tucker", "K", "BAL", "1264", {"xpm": 3.0}),
            _weekly_row("def:SFO", "49ers", "DEF", "SFO", "SF", {"sack": 3.0}),
        ]
    )
    store.close()
    return env


def test_sleeper_lineup_prints_sit_start_with_full_ppr(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(app, ["lineup", "2026", "--league", "sleeper", "--offline"], env=env)
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Week 2" in result.output
    assert "Steelers Nation" in result.output
    assert "Derrick Henry" in result.output
    assert "Slow Back" in result.output
    assert "Start" in result.output
    assert "Sit" in result.output
    # Chase: 10 rec * 1.0 PPR + 80 * 0.1 = 18.0 (Yahoo half-PPR would be 13.0)
    assert "18.0" in output
    assert "Sleeper league settings" in result.output
    assert "full PPR" not in result.output
    assert "configured Yahoo" not in result.output


def test_sleeper_lineup_does_not_write_league_state(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(app, ["lineup", "2026", "--league", "sleeper", "--offline"], env=env)
    assert result.exit_code == 0, result.output
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    assert store.league_context(2026) is None
    store.close()


def test_sleeper_lineup_writes_no_sit_start_snapshot(tmp_path):
    """Nothing reads a Sleeper advice snapshot yet; Yahoo keeps lineup/ to itself."""
    env = _seed_store(tmp_path)
    result = runner.invoke(app, ["lineup", "2026", "--league", "sleeper", "--offline"], env=env)
    assert result.exit_code == 0, result.output
    cache = SnapshotCache(tmp_path / "snapshots")
    assert not cache.has(lineup_snapshot_key(2026, 2))
    assert not (tmp_path / "snapshots" / "lineup").exists()


def test_sleeper_lineup_rejects_non_current_week(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(
        app, ["lineup", "2026", "--league", "sleeper", "--offline", "--week", "1"], env=env
    )
    assert result.exit_code == 1
    output = _plain(result.output)
    assert "current roster week" in output
    assert "week 2" in output
    assert "--week 1" in output
    assert "Derrick Henry" not in result.output


def test_sleeper_lineup_accepts_explicit_current_week(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(
        app, ["lineup", "2026", "--league", "sleeper", "--offline", "--week", "2"], env=env
    )
    assert result.exit_code == 0, result.output
    assert "Week 2" in result.output
    assert "Derrick Henry" in result.output


def test_sleeper_lineup_rejects_force(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(
        app, ["lineup", "2026", "--league", "sleeper", "--offline", "--force"], env=env
    )
    assert result.exit_code != 0
    assert "force" in result.output.lower()
    assert "Traceback" not in result.output


def test_sleeper_lineup_has_no_refresh_flag(tmp_path):
    """Sit/start refetches every run, so there is nothing for --refresh to do."""
    env = _seed_store(tmp_path)
    result = runner.invoke(
        app, ["lineup", "2026", "--league", "sleeper", "--offline", "--refresh"], env=env
    )
    assert result.exit_code == 2


def test_sleeper_lineup_rejects_publish(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(
        app, ["lineup", "2026", "--league", "sleeper", "--offline", "--publish"], env=env
    )
    assert result.exit_code != 0
    assert "publish" in result.output.lower()


def test_sleeper_lineup_warns_about_unmatched_roster_players(tmp_path):
    """An unmatched id scores zero and is advised to sit; that must not look real."""
    env = _seed_store(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.conn.execute("DELETE FROM crosswalk WHERE sleeper_id = '3198'")
    store.close()
    result = runner.invoke(app, ["lineup", "2026", "--league", "sleeper", "--offline"], env=env)
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    assert "did not match the crosswalk" in output
    assert "score zero" in output


def test_sleeper_lineup_requires_env(tmp_path):
    env = _seed_store(tmp_path)
    del env["FFB_SLEEPER_LEAGUE_ID"]
    result = runner.invoke(app, ["lineup", "2026", "--league", "sleeper", "--offline"], env=env)
    assert result.exit_code == 2
    assert "FFB_SLEEPER_LEAGUE_ID" in result.output


def test_default_lineup_still_requires_yahoo_league_state(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(app, ["lineup", "2026"], env=env)
    assert result.exit_code == 1
    assert "league sync" in result.output


def test_report_scoring_provenance_does_not_special_case_sleeper(monkeypatch):
    """Sleeper lineup prints provenance itself; this helper stays Yahoo/fixture."""
    from io import StringIO

    from rich.console import Console

    from ffb import cli as cli_mod

    buffer = StringIO()
    monkeypatch.setattr(cli_mod, "console", Console(file=buffer, force_terminal=False))
    cli_mod._report_scoring_provenance(type("L", (), {"scoring_provenance": "sleeper"})())
    text = buffer.getvalue()
    assert "Sleeper" not in text
    assert "mock fixture league settings" in text
