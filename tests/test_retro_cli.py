"""CLI weekly retro against synthetic lineup + actuals fixtures."""

import json
from pathlib import Path

from ffb.cli import app
from ffb.retro import actuals_snapshot_key, lineup_snapshot_key
from ffb.snapshot import SnapshotCache
from ffb.store import Store

from .cli_plain import PlainCliRunner

runner = PlainCliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_lineup_sitstart.json"
# The sit/start fixture is the mock league 1.l.sit, not the configured Yahoo
# league, so its snapshots nest under its own segment.
LEAGUE = "yahoo:1.l.sit"
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


def test_lineup_refuses_to_replace_an_existing_snapshot(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    first = runner.invoke(app, ["lineup", "2024"], env=env)
    assert first.exit_code == 0, first.output
    cache = SnapshotCache(tmp_path / "snapshots")
    original = cache.read_json(lineup_snapshot_key(2024, 1, LEAGUE))
    assert original["report"]["start"][0]["name"] == "Derrick Henry"

    store = Store(env["FFB_DB_PATH"])
    store.upsert_projections(
        [
            _weekly_row("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 40.0}),
            _weekly_row("rb-low", "Slow Back", "RB", "KCC", "slow", {"rush_yd": 180.0}),
        ]
    )
    store.close()
    second = runner.invoke(app, ["lineup", "2024"], env=env)
    assert second.exit_code == 0, second.output
    assert "already exists" in second.output
    stored = cache.read_json(lineup_snapshot_key(2024, 1, LEAGUE))
    assert stored["generated_at"] == original["generated_at"]
    assert stored["report"]["start"][0]["name"] == "Derrick Henry"
    assert stored["report"]["optimal_total"] == original["report"]["optimal_total"]


def test_lineup_writes_a_recommendation_snapshot(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    result = runner.invoke(app, ["lineup", "2024"], env=env)
    assert result.exit_code == 0, result.output
    cache = SnapshotCache(tmp_path / "snapshots")
    snapshot = cache.read_json(lineup_snapshot_key(2024, 1, LEAGUE))
    assert snapshot["kind"] == "lineup_recommendation"
    assert snapshot["week"] == 1
    assert any(row["native_id"] == "29279" for row in snapshot["players"])
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
    stored = cache.read_json(actuals_snapshot_key(2024, 1, LEAGUE))
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
    assert "FFB_TRACKER_URL" in result.output
    assert "--fixture" in result.output


def test_retro_rejects_non_positive_week(tmp_path):
    result = runner.invoke(app, ["retro", "2024", "--week", "0"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "week" in result.output.lower()


def test_retro_rejects_actuals_for_a_different_team(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    assert runner.invoke(app, ["lineup", "2024"], env=env).exit_code == 0
    payload = json.loads(ACTUALS.read_text())
    payload["matchups"][0]["teams"] = [
        {"team_key": "1.l.other.t.1", "points": 10.0},
        {"team_key": "1.l.other.t.2", "points": 8.0},
    ]
    for row in payload["players"]:
        row["team_key"] = "1.l.other.t.1"
    path = tmp_path / "other-league.json"
    path.write_text(json.dumps(payload))
    result = runner.invoke(app, ["retro", "2024", "--fixture", str(path)], env=env)
    assert result.exit_code == 1
    assert "team_key" in result.output


def _mock_tracker(monkeypatch, handler):
    import httpx

    from ffb import cli

    monkeypatch.setattr(
        cli, "_tracker_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )


def _tracker_env(tmp_path):
    return {
        **_seed_lineup_store(tmp_path),
        "FFB_TRACKER_URL": "https://tracker.test",
        "FFB_TRACKER_API_KEY": "sekrit",
    }


def _ready_lineup(env):
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    assert runner.invoke(app, ["lineup", "2024"], env=env).exit_code == 0


def test_retro_pulls_actuals_from_tracker_when_no_local_snapshot(tmp_path, monkeypatch):
    import httpx

    env = _tracker_env(tmp_path)
    _ready_lineup(env)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=json.loads(ACTUALS.read_text()))

    _mock_tracker(monkeypatch, handler)
    result = runner.invoke(app, ["retro", "2024"], env=env)
    assert result.exit_code == 0, result.output
    assert seen == {
        "url": "https://tracker.test/api/actuals?season=2024&week=1",
        "auth": "Bearer sekrit",
    }
    assert "Recommended 46.5" in " ".join(result.output.split())
    assert "sekrit" not in result.output
    cache = SnapshotCache(tmp_path / "snapshots")
    assert cache.read_json(actuals_snapshot_key(2024, 1, LEAGUE))["league"]["week"] == 1

    # Second run replays the snapshot and never calls the Worker.
    def explode(_request):
        raise AssertionError("tracker must not be called when a snapshot exists")

    _mock_tracker(monkeypatch, explode)
    replay = runner.invoke(app, ["retro", "2024"], env=env)
    assert replay.exit_code == 0, replay.output


def test_retro_reports_when_tracker_has_no_actuals_for_week(tmp_path, monkeypatch):
    import httpx

    env = _tracker_env(tmp_path)
    _ready_lineup(env)
    _mock_tracker(monkeypatch, lambda _r: httpx.Response(404, json={"error": "not_found"}))
    result = runner.invoke(app, ["retro", "2024"], env=env)
    assert result.exit_code == 1
    assert "tracker has no weekly actuals" in result.output.lower()
    assert not SnapshotCache(tmp_path / "snapshots").has(actuals_snapshot_key(2024, 1, LEAGUE))


def test_retro_rejects_invalid_tracker_payload_without_snapshotting(tmp_path, monkeypatch):
    import httpx

    env = _tracker_env(tmp_path)
    _ready_lineup(env)
    _mock_tracker(monkeypatch, lambda _r: httpx.Response(200, json={"schema_version": 2}))
    result = runner.invoke(app, ["retro", "2024"], env=env)
    assert result.exit_code == 1
    assert "invalid" in result.output.lower()
    assert not SnapshotCache(tmp_path / "snapshots").has(actuals_snapshot_key(2024, 1, LEAGUE))


def test_retro_reports_tracker_http_failure(tmp_path, monkeypatch):
    import httpx

    env = _tracker_env(tmp_path)
    _ready_lineup(env)
    _mock_tracker(monkeypatch, lambda _r: httpx.Response(401, json={"error": "unauthorized"}))
    result = runner.invoke(app, ["retro", "2024"], env=env)
    assert result.exit_code == 1
    assert "tracker fetch failed" in result.output.lower()
    assert "sekrit" not in result.output


def test_lineup_force_replaces_an_existing_snapshot(tmp_path):
    env = _seed_lineup_store(tmp_path)
    _ready_lineup(env)
    cache = SnapshotCache(tmp_path / "snapshots")
    original = cache.read_json(lineup_snapshot_key(2024, 1, LEAGUE))
    store = Store(env["FFB_DB_PATH"])
    store.upsert_projections(
        [
            _weekly_row("12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 40.0}),
            _weekly_row("rb-low", "Slow Back", "RB", "KCC", "slow", {"rush_yd": 180.0}),
        ]
    )
    store.close()
    hint = runner.invoke(app, ["lineup", "2024"], env=env)
    assert "--force" in hint.output
    forced = runner.invoke(app, ["lineup", "2024", "--force"], env=env)
    assert forced.exit_code == 0, forced.output
    assert "replaced" in forced.output.lower()
    stored = cache.read_json(lineup_snapshot_key(2024, 1, LEAGUE))
    assert stored["generated_at"] != original["generated_at"]
    assert stored["report"]["start"] != original["report"]["start"]


def test_lineup_skips_snapshot_for_a_past_week_unless_forced(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    # Advance the league to week 2; week-1 rosters stay stored beside the new week.
    payload = json.loads(FIXTURE.read_text())
    payload["league"]["current_week"] = 2
    for roster in payload["rosters"]:
        roster["week"] = 2
    path = tmp_path / "week2.json"
    path.write_text(json.dumps(payload))
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(path)], env=env).exit_code
        == 0
    )
    cache = SnapshotCache(tmp_path / "snapshots")
    result = runner.invoke(app, ["lineup", "2024", "--week", "1"], env=env)
    assert result.exit_code == 0, result.output
    assert "post-hoc" in result.output.lower()
    assert not cache.has(lineup_snapshot_key(2024, 1, LEAGUE))
    forced = runner.invoke(app, ["lineup", "2024", "--week", "1", "--force"], env=env)
    assert forced.exit_code == 0, forced.output
    assert cache.has(lineup_snapshot_key(2024, 1, LEAGUE))


def test_retro_fixture_refuses_to_replace_different_actuals_unless_forced(tmp_path):
    env = _seed_lineup_store(tmp_path)
    _ready_lineup(env)
    first = runner.invoke(app, ["retro", "2024", "--fixture", str(ACTUALS)], env=env)
    assert first.exit_code == 0, first.output
    cache = SnapshotCache(tmp_path / "snapshots")
    original = cache.read_json(actuals_snapshot_key(2024, 1, LEAGUE))

    # Identical fixture replays quietly.
    same = runner.invoke(app, ["retro", "2024", "--fixture", str(ACTUALS)], env=env)
    assert same.exit_code == 0, same.output

    payload = json.loads(ACTUALS.read_text())
    henry = next(row for row in payload["players"] if row["name"] == "Derrick Henry")
    henry["points"] = 1.0
    path = tmp_path / "revised.json"
    path.write_text(json.dumps(payload))
    refused = runner.invoke(app, ["retro", "2024", "--fixture", str(path)], env=env)
    assert refused.exit_code == 1, refused.output
    assert "--force" in refused.output
    assert cache.read_json(actuals_snapshot_key(2024, 1, LEAGUE)) == original

    forced = runner.invoke(app, ["retro", "2024", "--fixture", str(path), "--force"], env=env)
    assert forced.exit_code == 0, forced.output
    assert cache.read_json(actuals_snapshot_key(2024, 1, LEAGUE))["players"] != original["players"]


def _capture_render(monkeypatch, report):
    from io import StringIO

    from rich.console import Console

    from ffb import cli

    buf = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, color_system=None, highlight=False))
    cli._render_retro(report)
    return buf.getvalue()


def _retro_render(**changes):
    report = {
        "week": 1,
        "team_name": "Brian's Team",
        "recommended_total": 10.0,
        "started_total": 10.0,
        "delta": 0.0,
        "hindsight_total": 16.8,
        "hindsight_started_total": 10.0,
        "hindsight_delta": 6.8,
        "start_hits": [],
        "start_misses": [],
        "sit_hits": [],
        "sit_misses": [],
        "hindsight_start": [
            {
                "name": "Malik Washington",
                "actual": 16.8,
                "projected": 8.0,
                "native_id": "40393",
            }
        ],
        "hindsight_sit": [
            {
                "name": "Rico Dowdle",
                "actual": 3.1,
                "projected": 12.0,
                "native_id": "40904",
            }
        ],
        "source_accuracy": [],
        "missing_actuals": [],
        "matchup": None,
    }
    report.update(changes)
    return report


def test_retro_cli_keeps_recommended_and_adds_hindsight_line(tmp_path):
    env = _seed_lineup_store(tmp_path)
    _ready_lineup(env)
    result = runner.invoke(app, ["retro", "2024", "--fixture", str(ACTUALS)], env=env)
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Recommended 46.5" in output
    assert "Hindsight" in result.output
    assert "Started 25.5" in output


def test_retro_cli_does_not_print_only_no_swaps_when_hindsight_disagrees(monkeypatch):
    output = _capture_render(monkeypatch, _retro_render())
    assert "Recommended 10.0" in output
    assert "Hindsight 16.8" in output
    assert "Malik Washington" in output
    assert "Rico Dowdle" in output
    assert "No sit/start swaps in the advice snapshot." not in output
    assert "Advice matched hindsight." not in output


def test_retro_cli_says_advice_matched_hindsight_when_sets_agree(monkeypatch):
    miss = {
        "name": "Good Def",
        "actual": 10.0,
        "projected": 12.0,
        "native_id": "100002",
    }
    sit = {
        "name": "Bad Def",
        "actual": 4.0,
        "projected": 8.0,
        "native_id": "100001",
    }
    output = _capture_render(
        monkeypatch,
        _retro_render(
            recommended_total=10.0,
            started_total=4.0,
            delta=6.0,
            hindsight_total=10.0,
            hindsight_delta=6.0,
            start_misses=[miss],
            sit_misses=[sit],
            hindsight_start=[miss],
            hindsight_sit=[sit],
        ),
    )
    assert "Recommended 10.0" in output
    assert "Hindsight 10.0" in output
    assert "Start miss" in output
    assert "Advice matched hindsight." in output


def test_retro_cli_shows_hindsight_reversal_of_followed_advice(monkeypatch):
    henry = {"name": "Derrick Henry", "actual": 3.0, "projected": 18.0, "native_id": "1"}
    slow = {"name": "Slow Guy", "actual": 21.0, "projected": 6.0, "native_id": "2"}
    output = _capture_render(
        monkeypatch,
        _retro_render(
            hindsight_total=28.0,
            hindsight_delta=18.0,
            start_hits=[henry],
            sit_hits=[slow],
            hindsight_start=[slow],
            hindsight_sit=[henry],
        ),
    )
    assert "Start hit Derrick Henry" in output
    assert "Hindsight start Slow Guy" in output
    assert "Hindsight sit Derrick Henry" in output
    assert "Advice matched hindsight." not in output


def test_retro_cli_no_swaps_week_prints_one_summary_line(monkeypatch):
    output = _capture_render(
        monkeypatch,
        _retro_render(
            hindsight_total=10.0, hindsight_delta=0.0, hindsight_start=[], hindsight_sit=[]
        ),
    )
    assert "No sit/start swaps in the advice snapshot." in output
    assert "Advice matched hindsight." not in output
