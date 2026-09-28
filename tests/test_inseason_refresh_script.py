"""scripts/inseason-refresh.sh gates bundle-dependent steps on tracker freshness."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inseason-refresh.sh"
SLEEPER_KEY = "sleeper:42"
YAHOO_KEY = "yahoo:470.l.928421"
STALE_EXIT = 3


def _refresh(tmp_path: Path, leagues: list[dict], *args: str) -> subprocess.CompletedProcess[str]:
    """Run a copy of the script with fake curl (Sleeper state, tracker) and uv.

    The fake uv appends each ffb command line to ``uv.log`` and succeeds.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / SCRIPT.name
    shutil.copy(SCRIPT, script)
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
    uv = bin_dir / "uv"
    uv.write_text(f'#!/usr/bin/env bash\nshift 3\necho "$*" >>"{tmp_path}/uv.log"\n')
    for fake in (curl, uv):
        fake.chmod(0o755)
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
    return subprocess.run(
        ["bash", str(script), *args], env=env, capture_output=True, text=True, check=False
    )


def _dry_run(tmp_path: Path, leagues: list[dict], *args: str) -> str:
    result = _refresh(tmp_path, leagues, "--dry-run", *args)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _ran(tmp_path: Path) -> list[str]:
    log = tmp_path / "uv.log"
    return log.read_text().splitlines() if log.exists() else []


def _league(key: str, synced_at: datetime, week: int = 4) -> dict:
    return {
        "league_key": key,
        "current_week": week,
        "synced_at": synced_at.isoformat().replace("+00:00", "Z"),
    }


def _fresh(key: str) -> dict:
    return _league(key, datetime.now(UTC))


SLEEPER_SYNC = "would run: ffb league sync 2026 --league sleeper"
YAHOO_COMMANDS = [
    "league sync 2026 --from-tracker",
    "ros 2026 --league yahoo --publish",
    "lineup 2026 --week 4 --league yahoo --force --publish",
    "digest 2026 --league yahoo --publish",
]
RERUN = 'make refresh ARGS="--skip-sync --yahoo-only --week 4"'
STALE_BUNDLES = pytest.mark.parametrize(
    "stale",
    [
        [_league(YAHOO_KEY, datetime.now(UTC) - timedelta(days=1))],
        [_league(YAHOO_KEY, datetime.now(UTC), week=3)],
        [],
    ],
    ids=["yesterday", "last-week", "missing"],
)


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
    out = _dry_run(tmp_path, [_fresh(SLEEPER_KEY)])
    assert f"bundle {SLEEPER_KEY}: week 4" in out
    assert f"{SLEEPER_SYNC}\n" in out
    assert "--push" not in out


@STALE_BUNDLES
def test_dry_run_reports_the_yahoo_steps_a_stale_bundle_would_skip(tmp_path, stale):
    out = _dry_run(tmp_path, [*stale, _fresh(SLEEPER_KEY)])
    for command in YAHOO_COMMANDS:
        assert f"would skip: ffb {command}\n" in out
        assert f"would run: ffb {command}" not in out
    assert "would run: ffb season sync 2026 --week 4 --refresh\n" in out
    assert "would run: ffb ros 2026 --league sleeper --publish\n" in out
    assert RERUN in out


@STALE_BUNDLES
def test_a_stale_yahoo_bundle_publishes_no_yahoo_cards(tmp_path, stale):
    result = _refresh(tmp_path, [*stale, _fresh(SLEEPER_KEY)])
    assert result.returncode == STALE_EXIT, result.stderr
    ran = _ran(tmp_path)
    assert not set(YAHOO_COMMANDS) & set(ran)
    assert "season sync 2026 --week 4 --refresh" in ran
    assert "digest 2026 --league sleeper --publish" in ran
    assert result.stdout.rstrip().endswith(RERUN)


def test_a_fresh_yahoo_bundle_publishes_yahoo_cards(tmp_path):
    result = _refresh(tmp_path, [_fresh(YAHOO_KEY), _fresh(SLEEPER_KEY)])
    assert result.returncode == 0, result.stderr
    assert set(YAHOO_COMMANDS) <= set(_ran(tmp_path))
    assert "yahoo-only" not in result.stdout


def test_allow_stale_keeps_publishing_from_a_stale_yahoo_bundle(tmp_path):
    result = _refresh(tmp_path, [_fresh(SLEEPER_KEY)], "--allow-stale")
    assert result.returncode == 0, result.stderr
    assert set(YAHOO_COMMANDS) <= set(_ran(tmp_path))


def test_yahoo_only_rerun_touches_nothing_but_yahoo(tmp_path):
    result = _refresh(
        tmp_path,
        [_fresh(YAHOO_KEY), _fresh(SLEEPER_KEY)],
        "--skip-sync",
        "--yahoo-only",
        "--week-roll",
    )
    assert result.returncode == 0, result.stderr
    ran = _ran(tmp_path)
    assert ran == YAHOO_COMMANDS
    assert f"dashboard {SLEEPER_KEY}" not in result.stdout
    assert f"dashboard {YAHOO_KEY}" in result.stdout
