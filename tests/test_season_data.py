"""Season-data application service behavior."""

import json
from datetime import UTC, datetime
from pathlib import Path

from ffb.season_data import SeasonDataService
from ffb.snapshot import SnapshotCache, SnapshotPolicy
from ffb.sources import crosswalk, sleeper
from ffb.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def test_sync_uses_injected_utc_clock_for_observable_attempt_times(tmp_path):
    cache = SnapshotCache(tmp_path / "snapshots")
    cache.get_json(
        crosswalk.snapshot_key(),
        lambda: json.loads((FIXTURES / "ff_playerids_sample.json").read_text()),
    )
    cache.get_json(
        sleeper.snapshot_key(2024),
        lambda: json.loads((FIXTURES / "sleeper_projections_sample.json").read_text()),
    )
    store = Store(tmp_path / "ffb.duckdb")
    store.init_schema()
    now = datetime(2026, 7, 22, 14, 30, tzinfo=UTC)
    service = SeasonDataService(store, cache, clock=lambda: now)

    results = service.sync(2024, selectors=["sleeper"], policy=SnapshotPolicy.OFFLINE)
    status = service.status(2024)
    store.close()

    assert all(result.state == "ready" for result in results)
    tracked = {
        source["name"]: source
        for source in status["sources"]
        if source["name"] in ("crosswalk", "sleeper")
    }
    assert tracked["crosswalk"]["last_attempt_at"] == "2026-07-22T14:30:00Z"
    assert tracked["sleeper"]["last_success_at"] == "2026-07-22T14:30:00Z"


def _fixture_service(tmp_path, fetchers=None):
    cache = SnapshotCache(tmp_path / "snapshots")
    store = Store(tmp_path / "ffb.duckdb")
    store.init_schema()
    return store, SeasonDataService(store, cache, fetchers=fetchers or {})


def _fixture_fetchers():
    return {
        "crosswalk": lambda: json.loads((FIXTURES / "ff_playerids_sample.json").read_text()),
        "sleeper": lambda: json.loads((FIXTURES / "sleeper_projections_sample.json").read_text()),
        "espn": lambda: json.loads((FIXTURES / "espn_projections_sample.json").read_text()),
        "ffc": lambda: json.loads((FIXTURES / "ffc_adp_sample.json").read_text()),
        "schedule": lambda: json.loads((FIXTURES / "schedule_sample.json").read_text()),
    }


def test_expand_sources_includes_schedule():
    from ffb.season_data import expand_sources

    assert expand_sources(None) == ["sleeper", "espn", "ffc", "schedule"]
    assert expand_sources(["schedule"]) == ["schedule"]


def test_sync_schedule_records_ready_state(tmp_path):
    store, service = _fixture_service(tmp_path, fetchers=_fixture_fetchers())

    results = {r.source: r for r in service.sync(2026, selectors=["schedule"])}
    status = service.status(2026)
    store.close()

    assert results["schedule"].state == "ready"
    assert results["schedule"].rows == 6
    assert results["schedule"].matched == 6
    tracked = next(s for s in status["sources"] if s["name"] == "schedule")
    assert tracked["kind"] == "schedule"
    assert tracked["state"] == "ready"
    assert tracked["stale"] is False
    assert tracked["snapshot"]["key"] == "nflverse/schedule_2026"


def test_sync_schedule_failure_aggregates_without_aborting(tmp_path):
    def boom():
        raise RuntimeError("nflverse down")

    fetchers = _fixture_fetchers()
    fetchers["schedule"] = boom
    store, service = _fixture_service(tmp_path, fetchers=fetchers)

    results = {r.source: r for r in service.sync(2026, selectors=["ffc", "schedule"])}
    store.close()

    assert results["ffc"].state == "ready"
    assert results["schedule"].state == "failed"
    assert "nflverse down" in results["schedule"].error


def test_status_incomplete_while_schedule_missing(tmp_path):
    fetchers = _fixture_fetchers()
    del fetchers["schedule"]
    store, service = _fixture_service(tmp_path, fetchers=fetchers)

    service.sync(2026, selectors=["projections", "adp"])
    status = service.status(2026)
    store.close()

    tracked = next(s for s in status["sources"] if s["name"] == "schedule")
    assert tracked["state"] == "missing"
    assert status["complete"] is False
