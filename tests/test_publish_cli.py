"""``--publish`` on lineup/digest/retro/ros and ``league sync --from-tracker``.

The Worker is a mocked httpx transport; each test captures the envelope the CLI
POSTed and checks it is the rendered report wrapped in the closed envelope.
"""

import json
import re
from pathlib import Path

import httpx
from typer.testing import CliRunner

from ffb import cli
from ffb.cli import app
from ffb.retro import lineup_snapshot_key
from ffb.snapshot import SnapshotCache
from ffb.store import Store

from .test_lineup_cli import _seed_lineup_store
from .test_ros_cli import _seed_ros_store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_lineup_sitstart.json"
ACTUALS = Path(__file__).parent / "fixtures" / "weekly_actuals_minimal.json"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
ENVELOPE_KEYS = {
    "schema_version",
    "kind",
    "season",
    "week",
    "generated_at",
    "team_name",
    "context",
    "report",
}


def _capture(monkeypatch, *, status=200, body=None):
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(
            {
                "url": str(request.url),
                "auth": request.headers.get("Authorization"),
                "body": json.loads(request.content),
            }
        )
        if status != 200:
            return httpx.Response(status, json=body or {})
        env = posted[-1]["body"]
        return httpx.Response(
            200,
            json={
                "kind": env["kind"],
                "season": env["season"],
                "week": env["week"],
                "generated_at": env["generated_at"],
            },
        )

    monkeypatch.setattr(
        cli, "_tracker_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return posted


def _tracker(env):
    return {**env, "FFB_TRACKER_URL": "https://tracker.test", "FFB_TRACKER_API_KEY": "sekrit"}


def _synced_lineup_env(tmp_path):
    env = _tracker(_seed_lineup_store(tmp_path))
    synced = runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env)
    assert synced.exit_code == 0, synced.output
    return env


def test_lineup_publish_posts_rendered_report_with_context(tmp_path, monkeypatch):
    env = _synced_lineup_env(tmp_path)
    posted = _capture(monkeypatch)
    result = runner.invoke(app, ["lineup", "2024", "--publish"], env=env)
    assert result.exit_code == 0, result.output
    assert "Derrick Henry" in result.output
    assert "Published lineup week 1" in result.output
    assert "sekrit" not in result.output
    assert len(posted) == 1
    assert posted[0]["url"] == "https://tracker.test/api/inseason/lineup"
    assert posted[0]["auth"] == "Bearer sekrit"
    envelope = posted[0]["body"]
    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["schema_version"] == 1
    assert envelope["kind"] == "lineup"
    assert envelope["season"] == 2024
    assert envelope["week"] == 1
    assert envelope["team_name"] == "Brian's Team"
    assert envelope["generated_at"].endswith("Z")
    assert envelope["context"]["league_synced_at"] == "2026-09-12T00:00:00Z"
    assert envelope["context"]["projection_sources"] == ["sleeper"]
    snapshot = SnapshotCache(tmp_path / "snapshots").read_json(lineup_snapshot_key(2024, 1))
    assert envelope["context"]["snapshot_generated_at"] == snapshot["generated_at"]
    report = envelope["report"]
    assert report["start"][0]["name"] == "Derrick Henry"
    assert {"aligned", "close_calls", "missing_projections", "undecidable", "delta"} <= set(report)


def test_lineup_publish_reports_the_locked_snapshot_it_did_not_replace(tmp_path, monkeypatch):
    env = _synced_lineup_env(tmp_path)
    assert runner.invoke(app, ["lineup", "2024"], env=env).exit_code == 0
    original = SnapshotCache(tmp_path / "snapshots").read_json(lineup_snapshot_key(2024, 1))
    posted = _capture(monkeypatch)
    result = runner.invoke(app, ["lineup", "2024", "--publish"], env=env)
    assert result.exit_code == 0, result.output
    assert posted[0]["body"]["context"]["snapshot_generated_at"] == original["generated_at"]


def test_lineup_publish_without_tracker_config_exits_1_and_keeps_report(tmp_path, monkeypatch):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    monkeypatch.delenv("FFB_TRACKER_URL", raising=False)
    monkeypatch.delenv("FFB_TRACKER_API_KEY", raising=False)
    result = runner.invoke(app, ["lineup", "2024", "--publish"], env=env)
    assert result.exit_code == 1
    assert "Derrick Henry" in result.output
    assert "FFB_TRACKER_URL" in result.output


def test_publish_failure_prints_worker_error_and_exits_1(tmp_path, monkeypatch):
    env = _synced_lineup_env(tmp_path)
    _capture(monkeypatch, status=409, body={"error": "stale_report", "message": "newer stored"})
    result = runner.invoke(app, ["lineup", "2024", "--publish"], env=env)
    assert result.exit_code == 1
    assert "stale_report" in result.output
    assert "newer stored" in result.output
    assert "sekrit" not in result.output
    assert SnapshotCache(tmp_path / "snapshots").has(lineup_snapshot_key(2024, 1))


def test_publish_transport_failure_never_leaks_the_key(tmp_path, monkeypatch):
    env = _synced_lineup_env(tmp_path)

    def boom(_request):
        raise httpx.ConnectError("refused sekrit")

    monkeypatch.setattr(
        cli, "_tracker_client", lambda: httpx.Client(transport=httpx.MockTransport(boom))
    )
    result = runner.invoke(app, ["lineup", "2024", "--publish"], env=env)
    assert result.exit_code == 1
    assert "ConnectError" in result.output
    assert "sekrit" not in result.output


def test_retro_publish_posts_scored_week_and_actuals_synced_at(tmp_path, monkeypatch):
    env = _synced_lineup_env(tmp_path)
    assert runner.invoke(app, ["lineup", "2024"], env=env).exit_code == 0
    posted = _capture(monkeypatch)
    result = runner.invoke(app, ["retro", "2024", "--fixture", str(ACTUALS), "--publish"], env=env)
    assert result.exit_code == 0, result.output
    assert "Published retro week 1" in result.output
    envelope = posted[0]["body"]
    assert posted[0]["url"].endswith("/api/inseason/retro")
    assert envelope["kind"] == "retro"
    assert envelope["week"] == 1
    assert envelope["team_name"] == "Brian's Team"
    assert envelope["context"] == {"actuals_synced_at": "2026-09-16T16:00:00Z"}
    assert envelope["report"]["recommended_total"] == 46.5
    assert envelope["report"]["missing_actuals"] == []
    assert "source_accuracy" in envelope["report"]


def test_digest_publish_posts_after_the_llm_step(tmp_path, monkeypatch):
    from .test_digest_cli import _seed, _sync_league_and_news

    env = _tracker(_seed(tmp_path))
    _sync_league_and_news(tmp_path, env)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FFB_ANTHROPIC_API_KEY", raising=False)
    posted = _capture(monkeypatch)
    result = runner.invoke(app, ["digest", "2024", "--publish"], env=env)
    assert result.exit_code == 0, result.output
    assert "Published digest week 1" in result.output
    envelope = posted[0]["body"]
    assert posted[0]["url"].endswith("/api/inseason/digest")
    assert envelope["kind"] == "digest"
    assert envelope["week"] == 1
    assert envelope["team_name"] == "Brian's Team"
    assert envelope["context"] == {"sources": ["injuries", "news"]}
    assert envelope["report"]["llm"]["error"].startswith("LLM skipped")
    assert envelope["report"]["narrative"] is None
    assert any(row["full_name"] == "Derrick Henry" for row in envelope["report"]["roster"])


def test_ros_publish_posts_the_full_report_not_the_limit_slice(tmp_path, monkeypatch):
    env = _tracker(_seed_ros_store(tmp_path))
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    posted = _capture(monkeypatch)
    result = runner.invoke(app, ["ros", "2024", "--limit", "1", "--publish"], env=env)
    assert result.exit_code == 0, result.output
    assert "Published ros week 1" in result.output
    envelope = posted[0]["body"]
    assert posted[0]["url"].endswith("/api/inseason/ros")
    assert envelope["kind"] == "ros"
    assert envelope["week"] == 1
    assert envelope["team_name"] == "Brian's Team"
    assert envelope["context"] == {
        "projection_sources": ["sleeper"],
        "playoff_weeks_requested": [15, 16, 17],
    }
    names = [row["name"] for row in envelope["report"]["players"]]
    assert "Derrick Henry" in names and "Ja'Marr Chase" in names
    assert envelope["report"]["playoff_weeks"] == [15, 16, 17]
    assert envelope["report"]["usage_available"] is False
    assert envelope["report"]["bye_plan"]


def test_ros_publish_rejects_position_filter(tmp_path, monkeypatch):
    env = _tracker(_seed_ros_store(tmp_path))
    posted = _capture(monkeypatch)
    result = runner.invoke(app, ["ros", "2024", "-p", "RB", "--publish"], env=env)
    assert result.exit_code == 2
    # Rich colours the usage box on CI terminals, splitting option names with
    # escape codes; compare the plain text.
    assert "--publish" in _ANSI.sub("", result.output)
    assert posted == []


def test_ros_publish_requires_league_state_for_the_week(tmp_path, monkeypatch):
    env = _tracker(_seed_ros_store(tmp_path))
    posted = _capture(monkeypatch)
    result = runner.invoke(app, ["ros", "2024", "--publish"], env=env)
    assert result.exit_code == 1
    assert "league sync" in result.output
    assert posted == []


def _mock_bundle_get(monkeypatch, payload, status=200):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "auth": request.headers.get("Authorization")})
        if status == 404:
            return httpx.Response(404, json={"error": "no league bundle"})
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        cli, "_tracker_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return calls


def test_league_sync_from_tracker_imports_the_worker_bundle(tmp_path, monkeypatch):
    env = _tracker({"FFB_DB_PATH": str(tmp_path / "ffb.duckdb")})
    calls = _mock_bundle_get(monkeypatch, json.loads(FIXTURE.read_text()))
    result = runner.invoke(app, ["league", "sync", "2024", "--from-tracker"], env=env)
    assert result.exit_code == 0, result.output
    assert "tracker league state" in result.output
    assert calls == [{"url": "https://tracker.test/api/league/bundle", "auth": "Bearer sekrit"}]
    store = Store(env["FFB_DB_PATH"])
    context = store.league_context(2024)
    store.close()
    assert context["synced_at"] == "2026-09-12T00:00:00Z"
    assert context["current_week"] == 1


def test_league_sync_from_tracker_rejects_an_invalid_or_missing_bundle(tmp_path, monkeypatch):
    env = _tracker({"FFB_DB_PATH": str(tmp_path / "ffb.duckdb")})
    _mock_bundle_get(monkeypatch, {"schema_version": 2})
    result = runner.invoke(app, ["league", "sync", "2024", "--from-tracker"], env=env)
    assert result.exit_code == 1
    assert "rejected" in result.output
    _mock_bundle_get(monkeypatch, None, status=404)
    missing = runner.invoke(app, ["league", "sync", "2024", "--from-tracker"], env=env)
    assert missing.exit_code == 1
    assert "no league bundle" in missing.output.lower()
    assert "sekrit" not in missing.output


def test_league_sync_from_tracker_is_exclusive_and_needs_config(tmp_path, monkeypatch):
    env = {"FFB_DB_PATH": str(tmp_path / "ffb.duckdb")}
    both = runner.invoke(
        app, ["league", "sync", "2024", "--from-tracker", "--fixture", str(FIXTURE)], env=env
    )
    assert both.exit_code == 2
    refresh = runner.invoke(app, ["league", "sync", "2024", "--from-tracker", "--refresh"], env=env)
    assert refresh.exit_code == 2
    monkeypatch.delenv("FFB_TRACKER_URL", raising=False)
    monkeypatch.delenv("FFB_TRACKER_API_KEY", raising=False)
    unset = runner.invoke(app, ["league", "sync", "2024", "--from-tracker"], env=env)
    assert unset.exit_code == 2
    assert "FFB_TRACKER_URL" in unset.output
