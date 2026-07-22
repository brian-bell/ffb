from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ffb import config
from ffb.snapshot import SnapshotCache
from ffb.sources import crosswalk, espn, ffc, sleeper


def test_primer_populates_every_production_snapshot_key(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    script = Path(__file__).with_name("prime_snapshots.py")

    subprocess.run(
        [sys.executable, str(script), str(snapshot_dir)],
        check=True,
        cwd=config.REPO_ROOT,
    )

    cache = SnapshotCache(snapshot_dir)
    expected_keys = (
        sleeper.snapshot_key(2024),
        espn.snapshot_key(2024),
        ffc.snapshot_key(2024, teams=1, fmt=config.FFC_FORMAT),
        crosswalk.snapshot_key(),
    )
    assert all(cache.has(key) for key in expected_keys)
