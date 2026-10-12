"""Lineup writes week calibration once; retro prints a shadow section and does not publish it."""

import json
from pathlib import Path

from ffb.actuals import parse_actuals
from ffb.calibration import PRIORS, calibration_params_key, seal_calibration_params
from ffb.cli import app
from ffb.predlog import build_prediction_ledger, prediction_ledger_key
from ffb.retro import RAW_CONSENSUS_WARNING, actuals_snapshot_key, lineup_snapshot_key
from ffb.snapshot import SnapshotCache

from .cli_plain import PlainCliRunner
from .test_lineup_cli import FIXTURE, _seed_lineup_store, _weekly_row
from .test_publish_cli import _capture, _tracker
from .test_retro_cli import ACTUALS

runner = PlainCliRunner()
LEAGUE = "yahoo:1.l.sit"
SLEEPER = "sleeper:1395854363380965376"
STAMP = "2026-10-03T11:00:00Z"


def test_lineup_writes_identity_params_once_and_leaves_advice_on_raw_consensus(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )

    first = runner.invoke(app, ["lineup", "2024"], env=env)
    assert first.exit_code == 0, first.output
    assert RAW_CONSENSUS_WARNING not in first.output
    cache = SnapshotCache(tmp_path / "snapshots")
    key = calibration_params_key(2024, 1, LEAGUE)
    params = parse_params(cache, key)
    assert params["fit_weeks"] == []
    assert params["n_by_position"] == {}
    assert params["league_key"] == LEAGUE
    assert params["sha256"]
    original = (cache.root / f"{key}.json").read_bytes()
    assert (cache.root / f"{key}.json").stat().st_mode & 0o777 == 0o600

    second = runner.invoke(app, ["lineup", "2024"], env=env)
    assert second.exit_code == 0, second.output
    assert (cache.root / f"{key}.json").read_bytes() == original

    ledger = _one_ledger(tmp_path, "predlog/yahoo_1.l.sit/2024_week1")
    assert ledger["schema_version"] == 2
    assert ledger["calibration"] == {"params_key": key, "sha256": params["sha256"]}
    henry = next(row for row in ledger["players"] if row["native_id"] == "29279")
    assert henry["consensus"] == 18.0
    assert henry["calibrated_mean"] == 18.0
    assert henry["calibrated_sd"] == 0.0

    snapshot = cache.read_json(lineup_snapshot_key(2024, 1, LEAGUE))
    assert snapshot["schema_version"] == 1
    assert "calibration" not in snapshot
    assert "calibrated_mean" not in snapshot["players"][0]
    assert "19.0" not in first.output


def test_existing_params_are_not_rewritten_and_shift_only_the_ledger(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    cache = SnapshotCache(tmp_path / "snapshots")
    key = calibration_params_key(2024, 1, LEAGUE)
    planted = _rb_params(bias=1.0)
    cache.put_json(key, planted, mode=0o600)
    original = (cache.root / f"{key}.json").read_bytes()

    result = runner.invoke(app, ["lineup", "2024"], env=env)

    assert result.exit_code == 0, result.output
    assert RAW_CONSENSUS_WARNING not in result.output
    assert (cache.root / f"{key}.json").read_bytes() == original
    henry = next(
        row
        for row in _one_ledger(tmp_path, "predlog/yahoo_1.l.sit/2024_week1")["players"]
        if row["native_id"] == "29279"
    )
    assert henry["consensus"] == 18.0
    assert henry["calibrated_mean"] == 19.0
    snapshot = cache.read_json(lineup_snapshot_key(2024, 1, LEAGUE))
    stored = next(row for row in snapshot["players"] if row["native_id"] == "29279")
    assert stored["points"] == 18.0
    assert "19.0" not in result.output


def test_unreadable_params_fall_back_to_raw_without_replacing_the_file(tmp_path):
    env = _seed_lineup_store(tmp_path)
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    cache = SnapshotCache(tmp_path / "snapshots")
    key = calibration_params_key(2024, 1, LEAGUE)
    path = cache.root / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")
    original = path.read_bytes()

    result = runner.invoke(app, ["lineup", "2024"], env=env)

    assert result.exit_code == 0, result.output
    assert RAW_CONSENSUS_WARNING in result.output
    assert path.read_bytes() == original
    ledger = _one_ledger(tmp_path, "predlog/yahoo_1.l.sit/2024_week1")
    assert ledger["calibration"] is None
    henry = next(row for row in ledger["players"] if row["native_id"] == "29279")
    assert henry["calibrated_mean"] == henry["consensus"]
    assert henry["calibrated_sd"] is None


def test_fit_pools_other_leagues_and_ignores_the_target_week(tmp_path):
    env = _seed_lineup_store(tmp_path)
    from ffb.store import Store

    store = Store(env["FFB_DB_PATH"])
    store.init_schema()
    store.upsert_projections(
        [
            _weekly_row(
                "12626", "Derrick Henry", "RB", "BAL", "3198", {"rush_yd": 180.0}, scope="week2"
            )
        ]
    )
    store.close()
    week2 = json.loads(FIXTURE.read_text())
    week2["league"]["current_week"] = 2
    for roster in week2["rosters"]:
        roster["week"] = 2
    week2_path = tmp_path / "week2.json"
    week2_path.write_text(json.dumps(week2))
    assert (
        runner.invoke(
            app, ["league", "sync", "2024", "--fixture", str(week2_path)], env=env
        ).exit_code
        == 0
    )
    cache = SnapshotCache(tmp_path / "snapshots")
    _plant_sleeper_week(cache, week=1, native_id="rb-early", actual=10.0)
    _plant_sleeper_week(cache, week=2, native_id="rb-leak", actual=99.0)

    result = runner.invoke(app, ["lineup", "2024"], env=env)

    assert result.exit_code == 0, result.output
    params = parse_params(cache, calibration_params_key(2024, 2, LEAGUE))
    assert params["fit_weeks"] == [1]
    assert params["n_by_position"] == {"RB": 1}
    assert params["pooled"] is True


def test_retro_prints_shadow_and_publishes_the_raw_report(tmp_path, monkeypatch):
    env = _tracker(_seed_lineup_store(tmp_path))
    assert (
        runner.invoke(app, ["league", "sync", "2024", "--fixture", str(FIXTURE)], env=env).exit_code
        == 0
    )
    assert runner.invoke(app, ["lineup", "2024"], env=env).exit_code == 0
    posted = _capture(monkeypatch)

    result = runner.invoke(app, ["retro", "2024", "--fixture", str(ACTUALS), "--publish"], env=env)

    assert result.exit_code == 0, result.output
    assert "Shadow calibrated" in result.output
    assert "Shadow matches the advice snapshot." in result.output
    assert "sleeper" in result.output
    report = posted[0]["body"]["report"]
    assert "shadow" not in report
    assert "source_accuracy" in report
    assert report["recommended_total"] == 46.5


def parse_params(cache: SnapshotCache, key: str) -> dict:
    from ffb.calibration import parse_calibration_params

    return parse_calibration_params(cache.read_json(key))


def _one_ledger(tmp_path: Path, prefix: str) -> dict:
    files = sorted((tmp_path / "snapshots" / prefix).glob("*.json"))
    assert files
    return json.loads(files[-1].read_text())


def _rb_params(*, bias: float) -> dict:
    return seal_calibration_params(
        {
            "schema_version": 1,
            "kind": "calibration_params",
            "season": 2024,
            "week": 1,
            "league_key": LEAGUE,
            "pooled": True,
            "fit_weeks": [],
            "n_by_position": {"RB": 100},
            "priors": dict(PRIORS),
            "parameters": {
                "RB": {
                    "n": 100,
                    "n_q": 0,
                    "weight_identity": True,
                    "bias": bias,
                    "weights": {"sleeper": 1.0},
                    "precisions": {"sleeper": 1.0},
                    "precision_pooled": 1.0,
                    "haircut_q": 1.0,
                    "sd_a": 0.0,
                    "sd_c": 0.0,
                }
            },
        }
    )


def _plant_sleeper_week(cache: SnapshotCache, *, week: int, native_id: str, actual: float) -> None:
    ledger = build_prediction_ledger(
        season=2024,
        week=week,
        league_key=SLEEPER,
        user_team_key="s.t.1",
        generated_at=STAMP,
        scoring_weights={"rec": 1.0},
        projection_sources={},
        players=[
            {
                "team_key": "s.t.1",
                "native_id": native_id,
                "name": "Planted Back",
                "position": "RB",
                "team": "KC",
                "points": 10.0,
                "n": 2,
                "source_points": {"sleeper": 12.0, "espn": 8.0},
                "selected_position": "RB",
                "eligible_positions": ["RB"],
                "matched": True,
            }
        ],
    )
    cache.put_json(prediction_ledger_key(2024, week, STAMP, SLEEPER), ledger, mode=0o600)
    bundle = parse_actuals(
        {
            "schema_version": 2,
            "source": "sleeper",
            "synced_at": "2026-10-14T12:00:00Z",
            "league": {
                "league_id": "1395854363380965376",
                "league_key": "1395854363380965376",
                "name": "Sleeper",
                "season": 2024,
                "week": week,
                "num_teams": 2,
            },
            "matchups": [
                {
                    "matchup_id": "1",
                    "week": week,
                    "teams": [
                        {"team_key": "s.t.1", "points": 1.0},
                        {"team_key": "s.t.2", "points": 1.0},
                    ],
                }
            ],
            "players": [
                {
                    "native_id": native_id,
                    "native_player_key": f"s.{native_id}",
                    "name": "Planted Back",
                    "team_key": "s.t.1",
                    "selected_position": "RB",
                    "points": actual,
                }
            ],
        },
        season=2024,
    )
    cache.put_json(actuals_snapshot_key(2024, week, SLEEPER), bundle.data, mode=0o600)
