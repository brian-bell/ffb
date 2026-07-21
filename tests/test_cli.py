"""CLI smoke test — offline, against primed snapshots."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ffb.cli import app
from ffb.ingest import ensure_ingested
from ffb.snapshot import SnapshotCache
from ffb.sources.crosswalk import snapshot_key as xwalk_key
from ffb.sources.espn import snapshot_key as espn_key
from ffb.sources.sleeper import snapshot_key
from ffb.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
SLEEPER_FIX = FIXTURES / "sleeper_projections_sample.json"
XWALK_FIX = FIXTURES / "ff_playerids_sample.json"
ESPN_FIX = FIXTURES / "espn_projections_sample.json"
runner = CliRunner()


def _env(tmp_path, *, with_espn=False):
    snap = tmp_path / "snap"
    cache = SnapshotCache(snap)
    # Prime snapshots as real fetches would have, so the run stays offline.
    cache.get_json(snapshot_key(2024), lambda: json.loads(SLEEPER_FIX.read_text()))
    cache.get_json(xwalk_key(), lambda: json.loads(XWALK_FIX.read_text()))
    if with_espn:
        cache.get_json(espn_key(2024), lambda: json.loads(ESPN_FIX.read_text()))
    return {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(snap),
    }


def test_rankings_rb_prints_ranked_list(tmp_path):
    result = runner.invoke(app, ["rankings", "--pos", "RB", "--season", "2024"], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "McCaffrey" in result.output
    assert "Henry" in result.output
    # Top RB (McCaffrey) appears before Henry in the ranked output.
    assert result.output.index("McCaffrey") < result.output.index("Henry")


def test_rankings_runs_offline_twice(tmp_path):
    env = _env(tmp_path)
    first = runner.invoke(app, ["rankings", "--pos", "RB"], env=env)
    second = runner.invoke(app, ["rankings", "--pos", "RB"], env=env)
    assert first.exit_code == 0
    assert second.exit_code == 0  # DB already ingested; still works


def test_sources_flag_shows_per_source_and_consensus_columns(tmp_path):
    env = _env(tmp_path, with_espn=True)
    result = runner.invoke(app, ["rankings", "--pos", "RB", "--sources"], env=env)
    assert result.exit_code == 0, result.output
    assert "Sleeper" in result.output
    assert "Espn" in result.output
    assert "Consensus" in result.output
    assert "Henry" in result.output


def test_unmatched_players_are_reported(tmp_path):
    # The crosswalk fixture only covers a few players; the rest rank source-only.
    result = runner.invoke(app, ["rankings", "--pos", "RB"], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "unmatched to crosswalk" in result.output


def test_late_crosswalk_reresolves_stored_projections(tmp_path):
    # Simulate a prior run that ingested Sleeper before the crosswalk was available
    # (transient miss): Henry lands under a fallback key.
    env = _env(tmp_path)  # primes sleeper + crosswalk snapshots
    db = tmp_path / "ffb.duckdb"
    snap = tmp_path / "snap"

    pre = Store(db)
    pre.init_schema()
    ensure_ingested(pre, SnapshotCache(snap), season=2024, fetch=lambda: [])
    henry = next(r for r in pre.projection_rows(2024, position="RB") if r["native_id"] == "3198")
    assert henry["player_key"] == "sleeper:3198"  # unmatched before the crosswalk
    pre.close()

    # Next CLI run loads the crosswalk and must re-resolve the stranded rows.
    result = runner.invoke(app, ["rankings", "--pos", "RB", "--season", "2024"], env=env)
    assert result.exit_code == 0, result.output

    after = Store(db)
    henry = next(r for r in after.projection_rows(2024, position="RB") if r["native_id"] == "3198")
    after.close()
    assert henry["player_key"] == "12626"  # now matched to the canonical key
    assert henry["matched"] is True


def test_sources_falls_back_to_sleeper_when_espn_fails(tmp_path, monkeypatch):
    env = _env(tmp_path, with_espn=True)
    # A first --sources run persists ESPN rows in the DB.
    assert runner.invoke(app, ["rankings", "--pos", "RB", "--sources"], env=env).exit_code == 0

    # Now ESPN ingestion fails; stale ESPN rows must not feed consensus.
    import ffb.cli as cli

    def boom(*args, **kwargs):
        raise RuntimeError("espn down")

    monkeypatch.setattr(cli, "ensure_espn_ingested", boom)
    result = runner.invoke(app, ["rankings", "--pos", "RB", "--sources"], env=env)
    assert result.exit_code == 0, result.output
    assert "showing Sleeper only" in result.output


def test_help_lists_rankings(tmp_path):
    result = runner.invoke(app, ["--help"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "rankings" in result.output
