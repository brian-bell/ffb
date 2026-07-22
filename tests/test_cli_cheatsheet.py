"""`ffb cheatsheet` — offline, against primed snapshots."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app
from ffb.snapshot import SnapshotCache
from ffb.sources.crosswalk import snapshot_key as xwalk_key
from ffb.sources.espn import snapshot_key as espn_key
from ffb.sources.ffc import snapshot_key as ffc_key
from ffb.sources.sleeper import snapshot_key

FIXTURES = Path(__file__).parent / "fixtures"
SLEEPER_FIX = FIXTURES / "sleeper_projections_sample.json"
XWALK_FIX = FIXTURES / "ff_playerids_sample.json"
ESPN_FIX = FIXTURES / "espn_projections_sample.json"
FFC_FIX = FIXTURES / "ffc_adp_sample.json"
runner = CliRunner()


def _env(tmp_path, *, with_ffc=True):
    snap = tmp_path / "snap"
    cache = SnapshotCache(snap)
    cache.get_json(snapshot_key(2024), lambda: json.loads(SLEEPER_FIX.read_text()))
    cache.get_json(xwalk_key(), lambda: json.loads(XWALK_FIX.read_text()))
    cache.get_json(espn_key(2024), lambda: json.loads(ESPN_FIX.read_text()))
    if with_ffc:
        cache.get_json(ffc_key(2024), lambda: json.loads(FFC_FIX.read_text()))
    return {"FFB_DB_PATH": str(tmp_path / "ffb.duckdb"), "FFB_SNAPSHOT_DIR": str(snap)}


def test_cheatsheet_renders_board_table(tmp_path):
    result = runner.invoke(app, ["cheatsheet", "--season", "2024"], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "VORP" in result.output
    assert "ADP" in result.output
    assert "Henry" in result.output


def test_cheatsheet_reports_unmatched_footer(tmp_path):
    # The crosswalk fixture covers only a few players; the rest rank source-only.
    result = runner.invoke(app, ["cheatsheet", "--season", "2024"], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "unmatched" in result.output


def test_cheatsheet_export_writes_all_three_files(tmp_path):
    export = tmp_path / "out"
    result = runner.invoke(
        app,
        ["cheatsheet", "--season", "2024", "--export", "--export-dir", str(export)],
        env=_env(tmp_path),
    )
    assert result.exit_code == 0, result.output
    assert (export / "cheatsheet.md").exists()
    assert (export / "cheatsheet.csv").exists()
    board_path = export / "board.json"
    assert board_path.exists()

    doc = json.loads(board_path.read_text())
    assert doc["version"] == 1
    assert doc["season"] == 2024
    assert doc["num_teams"] == 12
    assert isinstance(doc["players"], list) and doc["players"]
    # Contract fields present on a player.
    assert "vorp" in doc["players"][0]
    assert "adp" in doc["players"][0]
    # The written paths are surfaced to the user.
    assert "board.json" in result.output


def test_cheatsheet_joins_defense_projection_consensus_and_adp(tmp_path):
    export = tmp_path / "out"
    result = runner.invoke(
        app,
        ["cheatsheet", "--season", "2024", "--export", "--export-dir", str(export)],
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    players = json.loads((export / "board.json").read_text())["players"]
    defenses = [player for player in players if player["key"] == "def:SFO"]
    assert len(defenses) == 1
    assert defenses[0]["pos"] == "DEF"
    assert defenses[0]["team"] == "SFO"
    assert defenses[0]["points"] is not None
    assert defenses[0]["n_sources"] == 2
    assert defenses[0]["adp"] == 118.0
    assert defenses[0]["matched"] is True


def test_cheatsheet_degrades_when_ffc_fails(tmp_path, monkeypatch):
    import ffb.cli as cli

    def boom(*args, **kwargs):
        raise RuntimeError("ffc down")

    monkeypatch.setattr(cli, "ensure_adp_ingested", boom)
    result = runner.invoke(
        app, ["cheatsheet", "--season", "2024"], env=_env(tmp_path, with_ffc=False)
    )
    assert result.exit_code == 0, result.output
    assert "ADP unavailable" in result.output
    # Still renders the board (projections/VORP) without ADP.
    assert "Henry" in result.output


def test_cheatsheet_export_uses_default_dir_from_env(tmp_path):
    # `--export` with no `--export-dir` writes to paths.export_dir(), overridable
    # via FFB_EXPORT_DIR — otherwise the configured default is unreachable.
    env = _env(tmp_path)
    default_export = tmp_path / "default-exports"
    env["FFB_EXPORT_DIR"] = str(default_export)
    result = runner.invoke(app, ["cheatsheet", "--season", "2024", "--export"], env=env)
    assert result.exit_code == 0, result.output
    assert (default_export / "board.json").exists()


def test_cheatsheet_drops_stale_adp_when_ffc_fails(tmp_path, monkeypatch):
    # A first run persists ADP. When a later FFC ingest raises, the board must not
    # serve the stale persisted ADP — the warning says ADP is unavailable.
    env = _env(tmp_path, with_ffc=True)
    assert runner.invoke(app, ["cheatsheet", "--season", "2024"], env=env).exit_code == 0

    import ffb.cli as cli

    def boom(*args, **kwargs):
        raise RuntimeError("ffc down")

    monkeypatch.setattr(cli, "ensure_adp_ingested", boom)
    export = tmp_path / "out2"
    result = runner.invoke(
        app,
        ["cheatsheet", "--season", "2024", "--export", "--export-dir", str(export)],
        env=env,
    )
    assert result.exit_code == 0, result.output
    assert "ADP unavailable" in result.output
    doc = json.loads((export / "board.json").read_text())
    assert doc["players"], "board still renders without ADP"
    assert all(p["adp"] is None for p in doc["players"])  # no stale ADP leaked


def test_cheatsheet_refresh_invalid_ffc_drops_stale_adp(tmp_path, monkeypatch):
    # A prior run persists good ADP. A --refresh that returns an HTTP-OK but
    # invalid payload (status != Success) must not serve the stale slice as fresh.
    env = _env(tmp_path, with_ffc=True)
    assert runner.invoke(app, ["cheatsheet", "--season", "2024"], env=env).exit_code == 0

    import ffb.sources.crosswalk as crosswalk_src
    import ffb.sources.espn as espn_src
    import ffb.sources.ffc as ffc_src
    import ffb.sources.sleeper as sleeper_src

    monkeypatch.setattr(crosswalk_src, "fetch_playerids", lambda: json.loads(XWALK_FIX.read_text()))
    monkeypatch.setattr(
        sleeper_src,
        "fetch_projections",
        lambda *a, **k: json.loads(SLEEPER_FIX.read_text()),
    )
    monkeypatch.setattr(
        espn_src,
        "fetch_projections",
        lambda *a, **k: json.loads(ESPN_FIX.read_text()),
    )
    monkeypatch.setattr(ffc_src, "fetch_adp", lambda *a, **k: {"status": "Error"})
    export = tmp_path / "out3"
    result = runner.invoke(
        app,
        ["cheatsheet", "--season", "2024", "--refresh", "--export", "--export-dir", str(export)],
        env=env,
    )
    assert result.exit_code == 0, result.output
    assert "ADP unavailable" in result.output
    doc = json.loads((export / "board.json").read_text())
    assert all(p["adp"] is None for p in doc["players"])
