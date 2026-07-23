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
