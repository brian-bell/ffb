"""Sleeper sit/start: ``league sync --league sleeper`` then ``lineup --league sleeper``.

The Sleeper path is the Yahoo path with a different stored league, not a
provider-specific command body. What stays Yahoo-only is the sit/start snapshot
and tracker KV, which are not league-scoped yet.
"""

import json
from pathlib import Path
from urllib.parse import quote

import httpx

from ffb import config
from ffb.cli import app
from ffb.league_context import load_league_context
from ffb.retro import lineup_snapshot_key
from ffb.snapshot import SnapshotCache
from ffb.sources.crosswalk import parse_crosswalk
from ffb.store import Store

from .cli_plain import PlainCliRunner

runner = PlainCliRunner()


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
        f"sleeper/league_{LEAGUE_ID}_matchups_week2": "matchups_week2.json",
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


SYNC = ["league", "sync", "2026", "--league", "sleeper", "--offline"]
LINEUP = ["lineup", "2026", "--league", "sleeper"]


def _synced(tmp_path):
    """Seed snapshots + crosswalk, then persist the Sleeper league like Yahoo's."""
    env = _seed_store(tmp_path)
    result = runner.invoke(app, SYNC, env=env)
    assert result.exit_code == 0, result.output
    return env


def test_sleeper_lineup_prints_sit_start_with_full_ppr(tmp_path):
    env = _synced(tmp_path)
    result = runner.invoke(app, LINEUP, env=env)
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
    assert "configured Yahoo" not in result.output


def test_sleeper_league_state_persists_beside_yahoo(tmp_path):
    """ffb-ct7.3 made league_* league-keyed, so the sync now writes its own rows."""
    env = _synced(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    try:
        assert store.league_keys(2026) == [config.SLEEPER_LEAGUE_KEY]
        state = store.league_context(2026, config.SLEEPER_LEAGUE_KEY)
        assert state["source"] == "sleeper"
        assert state["current_week"] == 2
    finally:
        store.close()


def test_sleeper_lineup_is_scored_with_its_own_weights_not_yahoos(tmp_path):
    """The whole point of the epic: never score one league with another's weights."""
    env = _synced(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    try:
        context = load_league_context(store, 2026, config.SLEEPER_LEAGUE_KEY)
    finally:
        store.close()
    assert context.scoring is not config.LEAGUE_SCORING
    assert context.scoring.weights["rec"] == 1.0
    assert config.LEAGUE_SCORING.weights.get("rec") != 1.0
    assert context.league_key == config.SLEEPER_LEAGUE_KEY
    # Settings the league scores that no projection source emits are reported.
    assert "fgmiss" in context.unmodeled_scoring


def test_sleeper_lineup_snapshots_under_its_own_league_not_yahoos(tmp_path):
    """Snapshot keys carry the league, so Sleeper cannot overwrite Yahoo's advice."""
    env = _synced(tmp_path)
    result = runner.invoke(app, LINEUP, env=env)
    assert result.exit_code == 0, result.output
    cache = SnapshotCache(tmp_path / "snapshots")
    assert cache.has(lineup_snapshot_key(2026, 2, config.SLEEPER_LEAGUE_KEY))
    # The configured Yahoo league's flat path stays untouched.
    assert not cache.has(lineup_snapshot_key(2026, 2))


def test_sleeper_lineup_publishes_to_its_own_kv_slot(tmp_path, monkeypatch):
    """--publish works for Sleeper now, and names its league on the request."""
    env = _synced(tmp_path)
    posted = []

    def fake_post(self, url, **kwargs):
        posted.append({"url": str(httpx.URL(url, params=kwargs.get("params") or {}))})

        class _Response:
            status_code = 200
            reason_phrase = "OK"

            @staticmethod
            def json():
                return {"kind": "lineup", "season": 2026, "week": 2}

        return _Response()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = runner.invoke(
        app,
        [*LINEUP, "--publish"],
        env={**env, "FFB_TRACKER_URL": "https://tracker.test", "FFB_TRACKER_API_KEY": "k"},
    )
    assert result.exit_code == 0, result.output
    assert posted, "publish did not reach the tracker"
    assert posted[0]["url"].endswith(
        f"/api/inseason/lineup?league={quote(config.SLEEPER_LEAGUE_KEY, safe='')}"
    )


def test_sleeper_lineup_for_a_week_without_data_says_which_week(tmp_path):
    """A past week has neither stored rosters nor weekly projections; say so."""
    env = _synced(tmp_path)
    result = runner.invoke(app, [*LINEUP, "--week", "1"], env=env)
    assert result.exit_code == 1
    assert "week 1" in result.output
    assert "Derrick Henry" not in result.output


def test_sleeper_lineup_accepts_explicit_current_week(tmp_path):
    env = _synced(tmp_path)
    result = runner.invoke(app, [*LINEUP, "--week", "2"], env=env)
    assert result.exit_code == 0, result.output
    assert "Week 2" in result.output
    assert "Derrick Henry" in result.output


def test_sleeper_lineup_warns_about_unmatched_roster_players(tmp_path):
    """An unmatched id scores zero and is advised to sit; that must not look real."""
    env = _seed_store(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.conn.execute("DELETE FROM crosswalk WHERE sleeper_id = '3198'")
    store.close()
    assert runner.invoke(app, SYNC, env=env).exit_code == 0
    result = runner.invoke(app, LINEUP, env=env)
    assert result.exit_code == 0, result.output
    assert "did not match the crosswalk" in result.output
    assert "score zero" in result.output


def test_sleeper_league_sync_requires_env(tmp_path):
    env = _seed_store(tmp_path)
    del env["FFB_SLEEPER_LEAGUE_ID"]
    result = runner.invoke(app, SYNC, env=env)
    assert result.exit_code == 2
    assert "FFB_SLEEPER_LEAGUE_ID" in result.output


def test_league_sync_rejects_offline_without_sleeper(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(app, ["league", "sync", "2026", "--offline"], env=env)
    assert result.exit_code != 0
    assert "offline" in result.output.lower()


def test_lineup_without_any_stored_league_still_asks_for_a_sync(tmp_path):
    env = _seed_store(tmp_path)
    result = runner.invoke(app, ["lineup", "2026"], env=env)
    assert result.exit_code == 1
    assert "league sync" in result.output


def test_lineup_names_an_unknown_league_rather_than_guessing(tmp_path):
    env = _synced(tmp_path)
    result = runner.invoke(app, ["lineup", "2026", "--league", "yahoo"], env=env)
    assert result.exit_code == 1
    assert "yahoo" in result.output.lower()
    assert config.SLEEPER_LEAGUE_KEY in result.output


def test_backfilled_week_reads_matchup_starters_not_current_rosters(tmp_path):
    """The whole point of /matchups: a past week's lineup is recoverable."""
    env = _seed_store(tmp_path)
    backfill = runner.invoke(app, [*SYNC, "--week", "2"], env=env)
    assert backfill.exit_code == 0, backfill.output
    assert "week 2" in backfill.output

    store = Store(env["FFB_DB_PATH"])
    try:
        rows = store.league_roster_rows(2026, week=2, league_key=config.SLEEPER_LEAGUE_KEY)
    finally:
        store.close()
    started = {row["native_id"] for row in rows if row["selected_position"] != "BN"}
    # Week 2 started Derrick Henry (3198); current /rosters starts Slow Back.
    assert "3198" in started
    assert "slow" not in started


def test_league_sync_week_applies_only_to_a_live_sleeper_sync(tmp_path):
    env = _seed_store(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "yahoo_league_minimal.json"
    result = runner.invoke(
        app, ["league", "sync", "2024", "--fixture", str(fixture), "--week", "2"], env=env
    )
    assert result.exit_code != 0
    assert "week" in result.output.lower()
    assert "Traceback" not in result.output
