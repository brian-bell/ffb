"""Explicit season-data CLI behavior."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ffb.cli import app
from ffb.snapshot import SnapshotCache
from ffb.sources import crosswalk, espn, ffc, sleeper

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _env(tmp_path):
    snapshot_dir = tmp_path / "snapshots"
    cache = SnapshotCache(snapshot_dir)
    fixtures = {
        crosswalk.snapshot_key(): "ff_playerids_sample.json",
        sleeper.snapshot_key(2024): "sleeper_projections_sample.json",
        espn.snapshot_key(2024): "espn_projections_sample.json",
        ffc.snapshot_key(2024): "ffc_adp_sample.json",
    }
    for key, filename in fixtures.items():
        payload = json.loads((FIXTURES / filename).read_text())
        cache.get_json(key, lambda payload=payload: payload)
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(snapshot_dir),
    }


def test_explicit_offline_sync_makes_full_season_ready_and_rankable(tmp_path):
    env = _env(tmp_path)

    synced = runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env)
    assert synced.exit_code == 0, synced.output

    status = runner.invoke(app, ["season", "status", "2024"], env=env)
    assert status.exit_code == 0, status.output
    assert "complete" in status.output.lower()
    assert all(source in status.output for source in ("crosswalk", "sleeper", "espn", "ffc"))
    assert status.output.lower().count("ready") >= 4

    rankings = runner.invoke(app, ["rankings", "2024", "--position", "RB"], env=env)
    assert rankings.exit_code == 0, rankings.output
    assert "McCaffrey" in rankings.output
    assert "Henry" in rankings.output


def test_offline_sync_reports_every_missing_snapshot_without_fetching(tmp_path, monkeypatch):
    from ffb.sources import crosswalk as crosswalk_source
    from ffb.sources import espn as espn_source
    from ffb.sources import ffc as ffc_source
    from ffb.sources import sleeper as sleeper_source

    def no_network(*args, **kwargs):
        raise AssertionError("offline sync invoked a fetcher")

    monkeypatch.setattr(crosswalk_source, "fetch_playerids", no_network)
    monkeypatch.setattr(sleeper_source, "fetch_projections", no_network)
    monkeypatch.setattr(espn_source, "fetch_projections", no_network)
    monkeypatch.setattr(ffc_source, "fetch_adp", no_network)
    env = {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "empty-snapshots"),
    }

    result = runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env)

    assert result.exit_code == 1
    assert result.output.lower().count("failed") == 4
    assert "offline snapshot missing" in result.output


def test_source_selectors_expand_and_deduplicate_without_syncing_adp(tmp_path):
    env = _env(tmp_path)

    result = runner.invoke(
        app,
        [
            "season",
            "sync",
            "2024",
            "--offline",
            "--source",
            "projections",
            "--source",
            "sleeper",
        ],
        env=env,
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("sleeper") == 1
    assert result.output.count("espn") == 1
    assert "ffc" not in result.output
    status = runner.invoke(app, ["season", "status", "2024", "--json"], env=env)
    payload = json.loads(status.output)
    ffc_status = next(source for source in payload["sources"] if source["name"] == "ffc")
    assert ffc_status["state"] == "missing"


def test_status_json_includes_versioned_snapshot_provenance_and_separate_league(tmp_path):
    env = _env(tmp_path)
    assert runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env).exit_code == 0

    result = runner.invoke(app, ["season", "status", "2024", "--json"], env=env)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["version"] == 1
    assert payload["season"] == 2024
    assert payload["complete"] is True
    assert payload["league"]["state"] == "missing"
    for source in payload["sources"]:
        assert source["snapshot"]["key"]
        assert source["snapshot"]["modified_at"].endswith("Z")
        assert len(source["snapshot"]["sha256"]) == 64
        assert source["last_attempt_at"]
        assert source["last_success_at"]


def test_failed_empty_refresh_retains_data_and_known_good_snapshot(tmp_path, monkeypatch):
    from ffb.sources import crosswalk as crosswalk_source
    from ffb.sources import espn as espn_source

    env = _env(tmp_path)
    first = runner.invoke(app, ["season", "sync", "2024", "--offline", "--source", "espn"], env=env)
    assert first.exit_code == 0, first.output

    monkeypatch.setattr(
        crosswalk_source,
        "fetch_playerids",
        lambda: json.loads((FIXTURES / "ff_playerids_sample.json").read_text()),
    )
    monkeypatch.setattr(espn_source, "fetch_projections", lambda season: [])
    failed = runner.invoke(
        app, ["season", "sync", "2024", "--refresh", "--source", "espn"], env=env
    )
    assert failed.exit_code == 1
    assert "no usable rows" in failed.output

    rebuilt = runner.invoke(
        app,
        ["season", "sync", "2024", "--offline", "--rebuild", "--source", "espn"],
        env=env,
    )
    assert rebuilt.exit_code == 0, rebuilt.output

    rankings = runner.invoke(app, ["rankings", "2024", "--position", "RB"], env=env)
    assert rankings.exit_code == 0
    assert "Henry" in rankings.output


def test_unmatched_reports_current_source_identity_details(tmp_path):
    env = _env(tmp_path)
    synced = runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env)
    assert synced.exit_code == 0, synced.output

    result = runner.invoke(app, ["season", "unmatched", "2024", "--source", "sleeper"], env=env)

    assert result.exit_code == 0, result.output
    assert "Native ID" in result.output
    assert "Player Key" in result.output
    assert "sleeper:" in result.output
    assert "RB" in result.output
    assert "def:SFO" not in result.output


def test_rankings_is_read_only_and_leaves_sync_status_unchanged(tmp_path, monkeypatch):
    from ffb.sources import crosswalk as crosswalk_source
    from ffb.sources import espn as espn_source
    from ffb.sources import ffc as ffc_source
    from ffb.sources import sleeper as sleeper_source

    env = _env(tmp_path)
    assert runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env).exit_code == 0
    before = runner.invoke(app, ["season", "status", "2024", "--json"], env=env).output

    def fail(*args, **kwargs):
        raise AssertionError("read command invoked a network boundary")

    monkeypatch.setattr(crosswalk_source, "fetch_playerids", fail)
    monkeypatch.setattr(sleeper_source, "fetch_projections", fail)
    monkeypatch.setattr(espn_source, "fetch_projections", fail)
    monkeypatch.setattr(ffc_source, "fetch_adp", fail)

    result = runner.invoke(app, ["rankings", "2024", "--position", "RB", "--show-sources"], env=env)
    board = runner.invoke(app, ["board", "show", "2024", "--limit", "1"], env=env)
    after = runner.invoke(app, ["season", "status", "2024", "--json"], env=env).output

    assert result.exit_code == 0, result.output
    assert board.exit_code == 0, board.output
    assert "Sleeper" in result.output
    assert "Espn" in result.output
    assert before == after


def test_board_show_and_default_export_use_persisted_full_board(tmp_path):
    env = _env(tmp_path)
    assert runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env).exit_code == 0

    shown = runner.invoke(
        app, ["board", "show", "2024", "--position", "RB", "--limit", "2"], env=env
    )
    assert shown.exit_code == 0, shown.output
    assert "VORP" in shown.output
    assert "ADP" in shown.output

    output_dir = tmp_path / "exports"
    exported = runner.invoke(
        app, ["board", "export", "2024", "--output-dir", str(output_dir)], env=env
    )
    assert exported.exit_code == 0, exported.output
    assert (output_dir / "cheatsheet.md").exists()
    assert (output_dir / "cheatsheet.csv").exists()
    board_path = output_dir / "board.json"
    assert board_path.exists()
    payload = json.loads(board_path.read_text())
    assert payload["version"] == 1
    assert payload["season"] == 2024
    assert len(payload["players"]) > 2


@pytest.mark.parametrize(
    ("format_name", "filename"),
    [("json", "board.json"), ("csv", "cheatsheet.csv"), ("markdown", "cheatsheet.md")],
)
def test_board_export_can_select_each_format(tmp_path, format_name, filename):
    env = _env(tmp_path)
    assert runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env).exit_code == 0
    output_dir = tmp_path / "selected"

    result = runner.invoke(
        app,
        [
            "board",
            "export",
            "2024",
            "--format",
            format_name,
            "--output-dir",
            str(output_dir),
        ],
        env=env,
    )

    assert result.exit_code == 0, result.output
    assert sorted(path.name for path in output_dir.iterdir()) == [filename]


def test_reads_warn_when_using_retained_data_after_failed_refresh(tmp_path, monkeypatch):
    from ffb.sources import crosswalk as crosswalk_source
    from ffb.sources import espn as espn_source

    env = _env(tmp_path)
    assert runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env).exit_code == 0
    monkeypatch.setattr(
        crosswalk_source,
        "fetch_playerids",
        lambda: json.loads((FIXTURES / "ff_playerids_sample.json").read_text()),
    )
    monkeypatch.setattr(espn_source, "fetch_projections", lambda season: [])
    failed = runner.invoke(
        app, ["season", "sync", "2024", "--refresh", "--source", "espn"], env=env
    )
    assert failed.exit_code == 1

    status = runner.invoke(app, ["season", "status", "2024", "--json"], env=env)
    payload = json.loads(status.output)
    espn_status = next(source for source in payload["sources"] if source["name"] == "espn")
    assert espn_status["state"] == "failed"
    assert espn_status["row_count"] > 0
    assert espn_status["last_success_at"]
    assert espn_status["error"]

    rankings = runner.invoke(
        app, ["rankings", "2024", "--position", "RB", "--show-sources"], env=env
    )
    assert rankings.exit_code == 0, rankings.output
    assert "espn is failed" in rankings.output
    assert "using retained data from" in rankings.output
    assert "Espn" in rankings.output


def test_board_warns_when_ffc_adp_was_synced_for_a_different_league_size(tmp_path):
    env = _env(tmp_path)
    assert runner.invoke(app, ["season", "sync", "2024", "--offline"], env=env).exit_code == 0
    league = runner.invoke(
        app,
        [
            "league",
            "sync",
            "2024",
            "--fixture",
            str(FIXTURES / "yahoo_league_minimal.json"),
        ],
        env=env,
    )
    assert league.exit_code == 0, league.output

    status = runner.invoke(app, ["season", "status", "2024", "--json"], env=env)
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    ffc_status = next(source for source in payload["sources"] if source["name"] == "ffc")
    assert ffc_status["stale"] is True

    board = runner.invoke(app, ["board", "show", "2024", "--limit", "1"], env=env)
    assert board.exit_code == 0, board.output
    assert "ffc ADP was synced for a different league size" in board.output
    assert "ffb season sync 2024 --source ffc" in " ".join(board.output.split())


def test_clean_break_rejects_removed_commands_and_options_and_defaults_to_2026(tmp_path):
    env = {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }

    status = runner.invoke(app, ["season", "status", "--json"], env=env)
    assert status.exit_code == 0
    assert json.loads(status.output)["season"] == 2026

    assert runner.invoke(app, ["cheatsheet"], env=env).exit_code == 2
    assert runner.invoke(app, ["rankings", "--season", "2024"], env=env).exit_code == 2
    assert runner.invoke(app, ["rankings", "--refresh"], env=env).exit_code == 2
    assert runner.invoke(app, ["rankings", "--sources"], env=env).exit_code == 2
    assert runner.invoke(app, ["rankings", "--pos", "RB"], env=env).exit_code == 2


def test_missing_only_refetches_a_deleted_snapshot_even_when_rows_exist(tmp_path, monkeypatch):
    from ffb.sources import sleeper as sleeper_source

    env = _env(tmp_path)
    assert (
        runner.invoke(
            app,
            ["season", "sync", "2024", "--offline", "--source", "sleeper"],
            env=env,
        ).exit_code
        == 0
    )
    snapshot = Path(env["FFB_SNAPSHOT_DIR"]) / f"{sleeper.snapshot_key(2024)}.json"
    snapshot.unlink()
    calls = 0

    def fetch(season):
        nonlocal calls
        calls += 1
        return json.loads((FIXTURES / "sleeper_projections_sample.json").read_text())

    monkeypatch.setattr(sleeper_source, "fetch_projections", fetch)
    result = runner.invoke(app, ["season", "sync", "2024", "--source", "sleeper"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == 1
    assert snapshot.exists()
