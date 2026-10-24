"""``ffb waivers`` on a Yahoo league: the Grok-scraped free-agent pool, not a Sleeper complement."""

import copy
import json
from pathlib import Path

import httpx

from ffb import cli
from ffb.cli import app
from ffb.free_agents import free_agents_snapshot_key
from ffb.snapshot import SnapshotCache
from ffb.store import Store

from .cli_plain import PlainCliRunner
from .test_lineup_cli import _seed_lineup_store, _weekly_row
from .test_publish_cli import ENVELOPE_KEYS, FIXTURE, LEAGUE, _capture, _tracker

runner = PlainCliRunner()
POOL = Path(__file__).parent / "fixtures" / "free_agents_minimal.json"
WAIVERS = ["waivers", "2024"]


def _season(*args, **kwargs):
    return _weekly_row(*args, **kwargs, scope="season")


def _crosswalk_row(player_key, yahoo_id, name, position, team):
    return {
        "player_key": player_key,
        "sleeper_id": None,
        "espn_id": None,
        "yahoo_id": yahoo_id,
        "gsis_id": None,
        "full_name": name,
        "position": position,
        "team": team,
    }


def _pool(*players):
    data = json.loads(POOL.read_text())
    data["league"].update(league_id="sit-1", league_key="1.l.sit", name="Sit Start Mock")
    data["players"] = [
        {
            "native_id": native_id,
            "native_player_key": f"1.p.{native_id}",
            "name": name,
            "nfl_team": "ATL",
            "primary_position": position,
            "eligible_positions": [position],
        }
        for native_id, name, position in players
    ]
    return data


def _write_pool(tmp_path, data):
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(data))
    return str(path)


def _yahoo_env(tmp_path):
    env = _tracker(_seed_lineup_store(tmp_path))
    store = Store(env["FFB_DB_PATH"])
    store.upsert_crosswalk(
        [
            _crosswalk_row("fa-rb", "40001", "Waiver Back", "RB", "ATL"),
            _crosswalk_row("fa-wr", "40002", "Waiver Dud", "WR", "ATL"),
            _crosswalk_row("held-rb", "40003", "Held Back", "RB", "ATL"),
        ]
    )
    store.upsert_projections(
        [
            _season("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 1500.0}),
            _season("rb-low", "Slow Back", "RB", "KCC", "slow", {"rush_yd": 200.0}),
            _season("wr-flex", "Flex Filler", "WR", "CHI", "flex", {"rec": 30.0, "rec_yd": 300.0}),
            _season("fa-rb", "Waiver Back", "RB", "ATL", "fa-rb", {"rush_yd": 900.0}),
            _season("fa-wr", "Waiver Dud", "WR", "ATL", "fa-wr", {"rec": 1.0, "rec_yd": 5.0}),
            # Strong, but not in the scraped pool: the Yahoo UI says he is not available.
            _season("held-rb", "Held Back", "RB", "ATL", "held-rb", {"rush_yd": 1400.0}),
        ]
    )
    store.close()
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert synced.exit_code == 0, synced.output
    return env


def test_waivers_ranks_the_scraped_pool_from_a_fixture(tmp_path):
    env = _yahoo_env(tmp_path)
    pool = _write_pool(
        tmp_path, _pool(("40001", "Waiver Back", "RB"), ("40002", "Waiver Dud", "WR"))
    )
    result = runner.invoke(app, [*WAIVERS, "--fixture", pool], env=env)
    assert result.exit_code == 0, result.output
    assert "Waiver Back" in result.output
    assert "Waiver Dud" not in result.output.split("Starters, weakest first")[0]
    assert "Held Back" not in result.output
    assert "Free-agent pool: 2 players" in result.output
    snapshots = SnapshotCache(Path(env["FFB_SNAPSHOT_DIR"]))
    assert snapshots.has(free_agents_snapshot_key(2024, LEAGUE))


def test_waivers_reuses_the_last_snapshot_without_flags(tmp_path):
    env = _yahoo_env(tmp_path)
    pool = _write_pool(tmp_path, _pool(("40001", "Waiver Back", "RB")))
    assert runner.invoke(app, [*WAIVERS, "--fixture", pool], env=env).exit_code == 0
    result = runner.invoke(app, WAIVERS, env=env)
    assert result.exit_code == 0, result.output
    assert "Waiver Back" in result.output


def test_waivers_without_a_pool_explains_how_to_get_one(tmp_path):
    result = runner.invoke(app, WAIVERS, env=_yahoo_env(tmp_path))
    assert result.exit_code == 1
    assert "No free-agent pool" in result.output
    assert "/api/free-agents" in result.output


def test_waivers_names_unmatched_free_agents_instead_of_guessing(tmp_path):
    env = _yahoo_env(tmp_path)
    pool = _write_pool(
        tmp_path, _pool(("40001", "Waiver Back", "RB"), ("99999", "Mystery Man", "RB"))
    )
    result = runner.invoke(app, [*WAIVERS, "--fixture", pool], env=env)
    assert result.exit_code == 0, result.output
    assert "1 free agent(s) did not match the crosswalk" in result.output
    assert "Mystery Man" in result.output


def test_waivers_rejects_an_invalid_fixture_and_conflicting_flags(tmp_path):
    env = _yahoo_env(tmp_path)
    broken = _pool(("40001", "Waiver Back", "RB"))
    broken["extra"] = 1
    invalid = runner.invoke(app, [*WAIVERS, "--fixture", _write_pool(tmp_path, broken)], env=env)
    assert invalid.exit_code == 1
    assert "Invalid free-agents fixture" in invalid.output
    both = runner.invoke(app, [*WAIVERS, "--fixture", str(POOL), "--from-tracker"], env=env)
    assert both.exit_code == 1
    assert "not both" in both.output


def _serve_pool(monkeypatch, payload, *, status=200):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "auth": request.headers.get("Authorization")})
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        cli, "_tracker_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return seen


def test_waivers_from_tracker_pulls_validates_and_snapshots(tmp_path, monkeypatch):
    env = _yahoo_env(tmp_path)
    seen = _serve_pool(monkeypatch, _pool(("40001", "Waiver Back", "RB")))
    result = runner.invoke(app, [*WAIVERS, "--from-tracker"], env=env)
    assert result.exit_code == 0, result.output
    assert "Waiver Back" in result.output
    assert (
        seen[0]["url"] == "https://tracker.test/api/free-agents?season=2024&league=yahoo%3A1.l.sit"
    )
    assert seen[0]["auth"] == "Bearer sekrit"
    assert "sekrit" not in result.output
    assert SnapshotCache(Path(env["FFB_SNAPSHOT_DIR"])).has(free_agents_snapshot_key(2024, LEAGUE))


def test_waivers_from_tracker_reports_a_missing_or_invalid_pool(tmp_path, monkeypatch):
    env = _yahoo_env(tmp_path)
    _serve_pool(monkeypatch, {"error": "not_found"}, status=404)
    missing = runner.invoke(app, [*WAIVERS, "--from-tracker"], env=env)
    assert missing.exit_code == 1
    assert "Tracker has no free-agent pool" in missing.output
    bad = copy.deepcopy(_pool(("40001", "Waiver Back", "RB")))
    bad["players"] = []
    _serve_pool(monkeypatch, bad)
    invalid = runner.invoke(app, [*WAIVERS, "--from-tracker"], env=env)
    assert invalid.exit_code == 1
    assert "invalid free-agent pool" in invalid.output


def test_waivers_publish_posts_to_the_yahoo_slot(tmp_path, monkeypatch):
    env = _yahoo_env(tmp_path)
    pool = _write_pool(tmp_path, _pool(("40001", "Waiver Back", "RB")))
    posted = _capture(monkeypatch)
    result = runner.invoke(app, [*WAIVERS, "--fixture", pool, "--publish"], env=env)
    assert result.exit_code == 0, result.output
    (call,) = posted
    assert call["url"] == "https://tracker.test/api/inseason/waivers?league=yahoo%3A1.l.sit"
    assert set(call["body"]) == ENVELOPE_KEYS
    assert call["body"]["kind"] == "waivers"
    assert call["body"]["report"]["candidates"][0]["name"] == "Waiver Back"
    assert call["body"]["report"]["free_agents"] == 1
