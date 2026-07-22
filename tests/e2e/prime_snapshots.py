"""Prime an isolated SnapshotCache from committed backend fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ffb import config
from ffb.snapshot import SnapshotCache
from ffb.sources import crosswalk, espn, ffc, sleeper

SEASON = 2024


def prime_snapshots(snapshot_dir: Path) -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures"
    cache = SnapshotCache(snapshot_dir)
    fixtures = {
        sleeper.snapshot_key(SEASON): fixture_dir / "sleeper_projections_sample.json",
        espn.snapshot_key(SEASON): fixture_dir / "espn_projections_sample.json",
        ffc.snapshot_key(SEASON, teams=1, fmt=config.FFC_FORMAT): fixture_dir
        / "ffc_adp_sample.json",
        crosswalk.snapshot_key(): fixture_dir / "ff_playerids_sample.json",
    }

    for key, fixture_path in fixtures.items():
        payload = json.loads(fixture_path.read_text())
        cache.get_json(key, lambda payload=payload: payload)

    missing = [key for key in fixtures if not cache.has(key)]
    if missing:
        raise RuntimeError(f"snapshot priming failed for: {', '.join(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_dir", type=Path)
    args = parser.parse_args()
    prime_snapshots(args.snapshot_dir)


if __name__ == "__main__":
    main()
