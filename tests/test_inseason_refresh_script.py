"""scripts/inseason-refresh.sh re-posts the Sleeper bundle only when it is stale."""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inseason-refresh.sh"
SLEEPER_KEY = "sleeper:42"


def _dry_run(tmp_path: Path, leagues: list[dict]) -> str:
    """Run --dry-run with a fake curl answering Sleeper state and /api/leagues."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "state.json").write_text(json.dumps({"season": "2026", "week": 4}))
    (tmp_path / "leagues.json").write_text(json.dumps({"leagues": leagues}))
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "for last; do :; done\n"
        'case "$last" in\n'
        f'  */state/nfl) cat "{tmp_path}/state.json" ;;\n'
        f'  */api/leagues) cat "{tmp_path}/leagues.json" ;;\n'
        "  */api/inseason\\?*) echo '{\"cards\": {}}' ;;\n"
        "  *) exit 22 ;;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FFB_ENV_FILE": str(tmp_path / "missing.env"),
        "FFB_TRACKER_URL": "https://tracker.test",
        "FFB_TRACKER_API_KEY": "sekrit",
        "FFB_SLEEPER_LEAGUE_ID": "42",
        "FFB_SLEEPER_USER_ID": "7",
        "ANTHROPIC_API_KEY": "x",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _league(key: str, synced_at: datetime, week: int = 4) -> dict:
    return {
        "league_key": key,
        "current_week": week,
        "synced_at": synced_at.isoformat().replace("+00:00", "Z"),
    }


SLEEPER_SYNC = "would run: ffb league sync 2026 --league sleeper"


@pytest.mark.parametrize(
    "leagues",
    [
        [_league(SLEEPER_KEY, datetime.now(UTC) - timedelta(days=2))],
        [_league(SLEEPER_KEY, datetime.now(UTC), week=3)],
        [],
    ],
    ids=["yesterday", "last-week", "missing"],
)
def test_a_stale_or_missing_sleeper_bundle_is_re_posted(tmp_path, leagues):
    out = _dry_run(tmp_path, leagues)
    assert f"{SLEEPER_SYNC} --push\n" in out


def test_a_fresh_sleeper_bundle_is_left_alone(tmp_path):
    out = _dry_run(tmp_path, [_league(SLEEPER_KEY, datetime.now(UTC))])
    assert f"bundle {SLEEPER_KEY}: week 4" in out
    assert f"{SLEEPER_SYNC}\n" in out
    assert "--push" not in out
