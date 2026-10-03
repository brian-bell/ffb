"""``ffb waivers``: Sleeper free agents vs the weakest starters, rendered and published."""

from ffb.cli import app
from ffb.store import Store

from .cli_plain import PlainCliRunner
from .test_publish_cli import ENVELOPE_KEYS, _capture, _synced_lineup_env, _tracker
from .test_sleeper_lineup_cli import SYNC, _seed_store, _weekly_row

runner = PlainCliRunner()
WAIVERS = ["waivers", "2026", "--league", "sleeper"]


def _season(*args, **kwargs):
    return _weekly_row(*args, **kwargs, scope="season")


def _waiver_env(tmp_path):
    env = _seed_store(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.upsert_projections(
        [
            _season("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 1500.0}),
            _season("rb-low", "Slow Back", "RB", "KCC", "slow", {"rush_yd": 200.0}),
            _season("rb-ok", "Okay Runner", "RB", "CHI", "rb-ok", {"rush_yd": 600.0}),
            _season("13971", "Ja'Marr Chase", "WR", "CIN", "7564", {"rec": 90.0, "rec_yd": 1200.0}),
            _season("wr-flex", "Flex Filler", "WR", "CHI", "flex", {"rec": 30.0, "rec_yd": 300.0}),
            _season("wr-ok", "Okay Receiver", "WR", "DAL", "wr-ok", {"rec": 40.0, "rec_yd": 400.0}),
            _season("wr-deep", "Deep Bench", "WR", "NYJ", "flex2", {"rec": 10.0, "rec_yd": 100.0}),
            _season("te-ok", "Okay Tightend", "TE", "DET", "te-ok", {"rec": 30.0, "rec_yd": 300.0}),
            _season("qb-test", "Test Quarterback", "QB", "PIT", "qb-test", {"pass_td": 20.0}),
            _season("10976", "Justin Tucker", "K", "BAL", "1264", {"xpm": 30.0}),
            _season("def:SFO", "49ers", "DEF", "SFO", "SF", {"sack": 30.0}),
            _season("fa-rb", "Waiver Back", "RB", "ATL", "fa-rb", {"rush_yd": 900.0}),
            _season("fa-wr", "Waiver Dud", "WR", "ATL", "fa-wr", {"rec": 1.0, "rec_yd": 5.0}),
        ]
    )
    store.close()
    result = runner.invoke(app, SYNC, env=env)
    assert result.exit_code == 0, result.output
    return env


def test_waivers_lists_free_agents_that_beat_a_weakest_starter(tmp_path):
    env = _waiver_env(tmp_path)
    result = runner.invoke(app, WAIVERS, env=env)
    assert result.exit_code == 0, result.output
    assert "Waiver Back" in result.output
    assert "Deep Bench" in result.output  # the flex starter he would replace
    assert "Waiver Dud" not in result.output
    assert "Derrick Henry" not in result.output.split("Starters, weakest first")[0]


def test_waivers_refuses_a_yahoo_league_until_live_authorization(tmp_path):
    env = _synced_lineup_env(tmp_path)
    result = runner.invoke(app, ["waivers", "2024"], env=env)
    assert result.exit_code == 1
    assert "Sleeper leagues only" in result.output
    assert "ffb-1ct.2" in result.output


def test_waivers_publish_posts_the_full_report_to_the_sleeper_slot(tmp_path, monkeypatch):
    env = _tracker(_waiver_env(tmp_path))
    posted = _capture(monkeypatch)
    result = runner.invoke(app, [*WAIVERS, "--limit", "1", "--publish"], env=env)
    assert result.exit_code == 0, result.output
    assert "Published waivers week 2" in result.output
    (call,) = posted
    assert "/api/inseason/waivers?league=sleeper%3A" in call["url"]
    envelope = call["body"]
    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["kind"] == "waivers"
    assert envelope["week"] == 2
    assert envelope["team_name"] == "Steelers Nation"
    assert set(envelope["context"]) == {"league_synced_at", "projection_sources"}
    assert envelope["context"]["projection_sources"] == ["sleeper"]
    report = envelope["report"]
    assert report["candidates"][0]["name"] == "Waiver Back"
    assert report["candidates"][0]["replaces"] == "Deep Bench"
    assert report["trending_available"] is False
