"""CLI news digest against the sit/start league fixture."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app
from ffb.sources.crosswalk import parse_crosswalk
from ffb.store import Store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_lineup_sitstart.json"
XWALK = Path(__file__).parent / "fixtures" / "ff_playerids_sample.json"
NEWS = Path(__file__).parent / "fixtures" / "espn_news_sample.json"
RSS = Path(__file__).parent / "fixtures" / "espn_news_rss_sample.json"


def _env(tmp_path):
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }


def _seed(tmp_path):
    env = _env(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.upsert_crosswalk(parse_crosswalk(json.loads(XWALK.read_text())))
    store.replace_injuries(
        [
            {
                "player_key": "12626",
                "native_id": "3198",
                "full_name": "Derrick Henry",
                "position": "RB",
                "team": "BAL",
                "raw_injury_status": "Questionable",
                "raw_roster_status": "Active",
                "status": "QUESTIONABLE",
                "fetched_at": "2026-09-12T08:00:00Z",
                "matched": True,
            }
        ],
        2024,
    )
    store.close()
    return env


def _sync_league_and_news(tmp_path, env):
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert synced.exit_code == 0, synced.output
    from ffb.ingest import ensure_news_ingested
    from ffb.snapshot import SnapshotCache

    store = Store(env["FFB_DB_PATH"])
    ensure_news_ingested(
        store,
        SnapshotCache(tmp_path / "snapshots"),
        2024,
        fetch=lambda: json.loads(NEWS.read_text()),
        fetch_rss=lambda: json.loads(RSS.read_text()),
        fetched_at="2026-09-12T12:00:00Z",
    )
    store.close()


def test_digest_shows_roster_injury_watch_and_other(tmp_path, monkeypatch):
    env = _seed(tmp_path)
    _sync_league_and_news(tmp_path, env)

    def fake_complete(*, model, system, user, api_key):
        if "haiku" in model:
            return (
                '[{"player_key":"12626","flag":"QUESTIONABLE",'
                '"note":"Limited Friday, game-time decision"}]'
            )
        return "Henry is a game-time call; the unrostered rookie is only a watch."

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("ffb.cli.complete_claude", fake_complete)

    result = runner.invoke(app, ["digest", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "Week 1 news digest" in result.output
    assert "Brian's Team" in result.output
    assert "Derrick Henry" in result.output
    assert "QUESTIONABLE" in result.output
    assert "Limited Friday" in result.output
    assert "Rookie Wideout" in result.output
    assert "Watch" in result.output
    assert "Week 1 schedule notes" in result.output
    assert "game-time call" in result.output
    assert "18.4" not in result.output


def test_digest_skips_llm_without_a_key(tmp_path):
    env = _seed(tmp_path)
    _sync_league_and_news(tmp_path, env)
    result = runner.invoke(app, ["digest", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "Derrick Henry" in result.output
    assert "LLM skipped" in result.output
    assert "game-time call" not in result.output


def test_digest_warns_when_news_mentions_are_stale(tmp_path):
    env = _seed(tmp_path)
    _sync_league_and_news(tmp_path, env)
    store = Store(env["FFB_DB_PATH"])
    store.replace_crosswalk(
        [
            row
            for row in parse_crosswalk(json.loads(XWALK.read_text()))
            if row["player_key"] != "12626"
        ]
    )
    store.upsert_season_source_state(
        {
            "season": 2024,
            "source": "news",
            "latest_attempt_status": "ready",
            "last_attempt_at": "2026-09-12T12:00:00Z",
            "last_success_at": "2026-09-12T12:00:00Z",
            "row_count": 6,
            "match_count": 2,
            "snapshot_key": "espn/news_nfl",
            "snapshot_modified_at": "2026-09-12T12:00:00Z",
            "snapshot_sha256": "abc",
            "latest_error": None,
        }
    )
    store.close()
    result = runner.invoke(app, ["digest", "2024"], env=env)
    assert result.exit_code == 0, result.output
    compact = " ".join(result.output.split())
    assert "news has stale identity resolution" in compact
    assert "--source news" in compact


def test_digest_warns_when_news_is_missing(tmp_path):
    env = _seed(tmp_path)
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert synced.exit_code == 0, synced.output
    result = runner.invoke(app, ["digest", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert "Warning: news is missing" in result.output
    assert "Derrick Henry" in result.output


def test_headlines_do_not_change_rankings(tmp_path):
    env = _seed(tmp_path)
    store = Store(env["FFB_DB_PATH"])
    store.upsert_projections(
        [
            {
                "player_key": "12626",
                "native_id": "3198",
                "full_name": "Derrick Henry",
                "position": "RB",
                "team": "BAL",
                "matched": True,
                "season": 2024,
                "source": "sleeper",
                "scope": "season",
                "stats": {"rush_yd": 180.0},
                "src_pts_ppr": None,
                "draftable": True,
            }
        ]
    )
    store.close()
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert synced.exit_code == 0, synced.output
    before = runner.invoke(app, ["rankings", "2024", "-p", "RB"], env=env)
    assert before.exit_code == 0, before.output
    from ffb.ingest import ensure_news_ingested
    from ffb.snapshot import SnapshotCache

    store = Store(env["FFB_DB_PATH"])
    ensure_news_ingested(
        store,
        SnapshotCache(tmp_path / "snapshots"),
        2024,
        fetch=lambda: json.loads(NEWS.read_text()),
        fetch_rss=lambda: json.loads(RSS.read_text()),
        fetched_at="2026-09-12T12:00:00Z",
    )
    store.close()
    after = runner.invoke(app, ["rankings", "2024", "-p", "RB"], env=env)
    assert after.exit_code == 0, after.output
    assert before.output == after.output
