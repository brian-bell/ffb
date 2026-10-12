"""Shadow calibration: shrinkage, walk-forward, ledger v2, and retro's shadow section."""

import json

import pytest

from ffb.calibration import (
    K_B,
    K_Q,
    K_SD,
    K_W,
    PRIORS,
    annotate_players,
    calibration_params_key,
    fit_calibration,
    parse_calibration_params,
    seal_calibration_params,
)
from ffb.config import YAHOO_LEAGUE_KEY
from ffb.lineup import compare_lineup
from ffb.outcomes import fact_table
from ffb.predlog import build_prediction_ledger, parse_prediction_ledger
from ffb.retro import build_lineup_snapshot, retro_report, shadow_report

YAHOO = YAHOO_LEAGUE_KEY
SLEEPER = "sleeper:1395854363380965376"
STAMP = "2026-10-03T11:00:00Z"
LATER = "2026-10-04T11:00:00Z"


def _fact(
    *,
    week=1,
    actual=10.0,
    consensus=10.0,
    sleeper=10.0,
    espn=10.0,
    position="RB",
    native_id="1",
    league=YAHOO,
    generated_at=STAMP,
    injury=None,
    season=2026,
):
    return {
        "league_key": league,
        "season": season,
        "week": week,
        "native_id": native_id,
        "position": position,
        "consensus": consensus,
        "source_points": {"sleeper": sleeper, "espn": espn},
        "actual_points": actual,
        "injury_status": injury,
        "generated_at": generated_at,
    }


def _rows(count, **overrides):
    start = overrides.pop("native_start", 0)
    return [_fact(native_id=str(start + index), **overrides) for index in range(count)]


def _fit(facts, *, week=2, league=YAHOO, season=2026):
    return fit_calibration(facts, season=season, week=week, league_key=league)


def test_params_path_is_flat_for_yahoo_and_nested_for_sleeper():
    assert calibration_params_key(2026, 6, YAHOO) == "calibration/2026_week6"
    assert (
        calibration_params_key(2026, 6, SLEEPER)
        == "calibration/sleeper_1395854363380965376/2026_week6"
    )


def test_empty_history_reproduces_raw_consensus_including_questionable():
    params = _fit([])
    assert params["fit_weeks"] == []
    assert params["n_by_position"] == {}
    assert params["priors"] == PRIORS
    assert params["pooled"] is True
    players = [
        {
            "position": "RB",
            "points": 13.5,
            "source_points": {"sleeper": 14.2, "espn": 12.8},
            "injury": {"status": "QUESTIONABLE"},
        },
        {"position": "WR", "points": 8.0, "source_points": {"sleeper": 8.0}, "injury": None},
        {"position": "TE", "points": None, "source_points": {}, "injury": None},
    ]

    annotated = annotate_players(players, params)

    assert annotated[0]["calibrated_mean"] == 13.5
    assert annotated[0]["calibrated_sd"] == 0.0
    assert annotated[1]["calibrated_mean"] == 8.0
    assert annotated[1]["calibrated_sd"] == 0.0
    assert annotated[2]["calibrated_mean"] is None
    assert annotated[2]["calibrated_sd"] is None
    assert parse_calibration_params(json.loads(json.dumps(params))) == params


def test_missing_params_fall_back_to_raw_consensus_with_null_sd():
    annotated = annotate_players(
        [{"position": "RB", "points": 13.5, "source_points": {"sleeper": 13.5}}],
        None,
    )

    assert annotated[0]["calibrated_mean"] == 13.5
    assert annotated[0]["calibrated_sd"] is None


def test_walk_forward_never_sees_the_target_week():
    history = _rows(100, week=1, actual=10.0, consensus=10.0)
    future = _rows(100, week=2, actual=20.0, consensus=10.0, native_start=1000)
    future.append(_fact(week=2, position="QB", native_id="qb", actual=30.0, consensus=10.0))

    held_out = _fit(history + future, week=2)
    included = _fit(history + future, week=3)

    assert held_out["fit_weeks"] == [1]
    assert held_out["n_by_position"] == {"RB": 100}
    assert "QB" not in held_out["parameters"]
    assert held_out["parameters"]["RB"]["bias"] == 0.0
    assert included["fit_weeks"] == [1, 2]
    assert included["n_by_position"]["RB"] == 200
    assert included["parameters"]["RB"]["bias"] == 2.0
    assert included["parameters"]["QB"]["n"] == 1


def test_latest_ledger_only_counts_toward_n_and_bias():
    early = _rows(99, actual=10.0, consensus=10.0)
    early.append(_fact(native_id="dup", actual=20.0, consensus=10.0, generated_at=STAMP))
    late = [_fact(native_id="dup", actual=10.0, consensus=10.0, generated_at=LATER)]

    params = _fit(early + late, week=2)

    assert params["parameters"]["RB"]["n"] == 100
    assert params["parameters"]["RB"]["bias"] == 0.0


def test_bias_shrinks_toward_zero_and_clamps():
    mild = _fit(_rows(K_B, actual=11.0, consensus=10.0), week=2)
    huge = _fit(_rows(K_B, actual=20.0, consensus=10.0), week=2)
    thin = _fit(_rows(K_B - 1, actual=20.0, consensus=10.0), week=2)

    # n == k_b, mean residual 1 → half of that, still inside ±2.
    assert mild["parameters"]["RB"]["bias"] == pytest.approx(0.5)
    # Unshrunk residual is 10; shrunk value is 5 and the clamp wins.
    assert huge["parameters"]["RB"]["bias"] == 2.0
    assert thin["parameters"]["RB"]["bias"] == 0.0
    assert thin["parameters"]["RB"]["weight_identity"] is True

    shifted = annotate_players([{"position": "RB", "points": 10.0, "source_points": {}}], huge)
    assert shifted[0]["calibrated_mean"] == 12.0


def test_source_weights_shrink_toward_equal_until_the_two_source_clamp():
    balanced = _rows(100, week=1, actual=10.0, sleeper=12.0, espn=8.0, consensus=10.0)
    skewed = _rows(
        100,
        week=1,
        actual=10.0,
        sleeper=12.0,
        espn=6.0,
        consensus=9.0,
        league=SLEEPER,
        native_start=500,
    )
    params = _fit(balanced + skewed, week=2)
    fitted = params["parameters"]["RB"]
    raw = (1 / 4) / (1 / 4 + 1 / 10)

    assert fitted["n"] == 200
    assert fitted["weight_identity"] is False
    assert 0.5 < fitted["weights"]["sleeper"] < raw
    assert fitted["weights"]["sleeper"] == pytest.approx(_shrunk_sleeper_weight())
    assert fitted["weights"]["espn"] == pytest.approx(1 - fitted["weights"]["sleeper"])

    extreme = _fit(_rows(K_W, actual=10.0, sleeper=10.0, espn=20.0, consensus=15.0), week=2)
    assert extreme["parameters"]["RB"]["weights"]["sleeper"] == pytest.approx(0.75)
    assert extreme["parameters"]["RB"]["weights"]["espn"] == pytest.approx(0.25)

    below = _fit(_rows(K_W - 1, actual=10.0, sleeper=10.0, espn=20.0, consensus=15.0), week=2)
    assert below["parameters"]["RB"]["weight_identity"] is True
    # n is still above the bias prior, so the mean is consensus plus clamped bias.
    # A reweight toward sleeper would not land on 13.
    assert below["parameters"]["RB"]["bias"] == -2.0
    annotated = annotate_players(
        [{"position": "RB", "points": 15.0, "source_points": {"sleeper": 10.0, "espn": 20.0}}],
        below,
    )
    assert annotated[0]["calibrated_mean"] == 13.0


def test_questionable_haircut_shrinks_toward_full_value_and_clamps():
    learned = _fit(
        _rows(K_Q, actual=8.0, consensus=10.0, sleeper=10.0, espn=10.0, injury="QUESTIONABLE"),
        week=2,
    )
    thin = _fit(
        _rows(K_Q - 1, actual=0.0, consensus=10.0, sleeper=10.0, espn=10.0, injury="QUESTIONABLE"),
        week=2,
    )
    clamped = _fit(
        _rows(300, actual=0.0, consensus=10.0, sleeper=10.0, espn=10.0, injury="QUESTIONABLE"),
        week=2,
    )

    assert learned["parameters"]["RB"]["haircut_q"] == pytest.approx(0.9)
    assert thin["parameters"]["RB"]["haircut_q"] == 1.0
    assert clamped["parameters"]["RB"]["haircut_q"] == 0.5
    # Bias is fit on the same rows, so the haircut is an extra multiplier.
    projected = annotate_players(
        [
            {
                "position": "RB",
                "points": 10.0,
                "source_points": {"sleeper": 10.0, "espn": 10.0},
                "injury": {"status": "QUESTIONABLE"},
            }
        ],
        learned,
    )
    assert projected[0]["calibrated_mean"] == pytest.approx(8.0)


def test_residual_sd_shrinks_toward_zero_and_is_identity_below_the_prior():
    low = _rows(50, actual=0.0, consensus=0.0, sleeper=0.0, espn=0.0)
    high_up = _rows(25, actual=24.0, consensus=16.0, sleeper=16.0, espn=16.0, native_start=50)
    high_down = _rows(25, actual=8.0, consensus=16.0, sleeper=16.0, espn=16.0, native_start=75)
    fitted = _fit(low + high_up + high_down, week=2)["parameters"]["RB"]
    thin = _fit(_rows(K_SD - 1, actual=20.0, consensus=10.0), week=2)["parameters"]["RB"]

    assert fitted["n"] == K_SD
    assert fitted["bias"] == 0.0
    assert fitted["sd_a"] == pytest.approx(0.0)
    assert fitted["sd_c"] == pytest.approx(1.0)
    assert thin["sd_a"] == 0.0
    assert thin["sd_c"] == 0.0

    annotated = annotate_players(
        [{"position": "RB", "points": 16.0, "source_points": {"sleeper": 16.0, "espn": 16.0}}],
        _fit(low + high_up + high_down, week=2),
    )
    assert annotated[0]["calibrated_sd"] == pytest.approx(4.0)


def test_tampered_params_fail_the_sha256_check():
    params = _fit([])
    params["fit_weeks"] = [1]

    with pytest.raises(ValueError, match="sha256"):
        parse_calibration_params(params)


def test_v2_ledger_carries_the_params_reference_and_calibrated_values():
    params = _fit([])
    players = annotate_players(
        [
            {
                "team_key": "t.1",
                "native_id": "4046",
                "name": "Some Back",
                "position": "RB",
                "team": "KC",
                "points": 13.5,
                "source_points": {"sleeper": 14.2, "espn": 12.8},
                "injury": None,
            }
        ],
        params,
    )
    ledger = build_prediction_ledger(
        season=2026,
        week=6,
        league_key=YAHOO,
        user_team_key="t.1",
        generated_at=STAMP,
        scoring_weights={"rec": 1.0},
        projection_sources={},
        players=players,
        calibration={"params_key": "calibration/2026_week6", "sha256": params["sha256"]},
    )

    assert ledger["schema_version"] == 2
    assert ledger["calibration"] == {
        "params_key": "calibration/2026_week6",
        "sha256": params["sha256"],
    }
    assert ledger["players"][0]["calibrated_mean"] == 13.5
    assert ledger["players"][0]["calibrated_sd"] == 0.0
    assert parse_prediction_ledger(ledger)["players"][0]["consensus"] == 13.5


def test_v1_ledger_still_joins_with_null_calibrated_fields():
    ledger = build_prediction_ledger(
        season=2026,
        week=1,
        league_key=YAHOO,
        user_team_key="t.1",
        generated_at=STAMP,
        scoring_weights={},
        projection_sources={},
        players=[
            {
                "team_key": "t.1",
                "native_id": "4046",
                "name": "Some Back",
                "position": "RB",
                "team": "KC",
                "points": 10.0,
                "source_points": {"sleeper": 10.0},
            }
        ],
    )
    v1 = {key: value for key, value in ledger.items() if key != "calibration"}
    v1["schema_version"] = 1
    v1["players"] = [
        {
            key: value
            for key, value in player.items()
            if key not in {"calibrated_mean", "calibrated_sd"}
        }
        for player in v1["players"]
    ]
    actuals = {
        "schema_version": 2,
        "source": "yahoo",
        "synced_at": "2026-10-14T12:00:00Z",
        "league": {
            "league_id": "928421",
            "league_key": "470.l.928421",
            "name": "MCFFL",
            "season": 2026,
            "week": 1,
            "num_teams": 2,
        },
        "matchups": [
            {
                "matchup_id": "1",
                "week": 1,
                "teams": [
                    {"team_key": "t.1", "points": 1.0},
                    {"team_key": "t.2", "points": 1.0},
                ],
            }
        ],
        "players": [
            {
                "native_id": "4046",
                "native_player_key": "470.p.4046",
                "name": "Some Back",
                "team_key": "t.1",
                "selected_position": "RB",
                "points": 8.0,
            }
        ],
    }

    table = fact_table(parse_prediction_ledger(v1), actuals)

    assert table["facts"][0]["calibrated_mean"] is None
    assert table["facts"][0]["calibrated_sd"] is None
    assert table["facts"][0]["actual_points"] == 8.0


def test_shadow_matches_advice_when_calibrated_means_equal_consensus():
    advice, players = _advice_pair()
    ledger = _user_ledger(players, means=None)
    for row in ledger["players"]:
        row["calibrated_mean"] = row["consensus"]
        row["calibrated_sd"] = 0.0
    ledger["calibration"] = {"params_key": "calibration/2024_week1", "sha256": "abc"}

    shadow = shadow_report(advice, _shadow_actuals(), ledger)
    report = retro_report(advice, _shadow_actuals())

    assert "shadow" not in report
    assert shadow["model"] == "calibrated"
    assert shadow["warning"] is None
    assert shadow["matches_advice"] is True
    assert shadow["recommended_total"] == report["recommended_total"]


def test_shadow_shows_the_lineup_calibrated_means_would_have_started():
    advice, players = _advice_pair()
    means = {"55501": 4.0, "29279": 14.0}
    ledger = _user_ledger(players, means=means)

    shadow = shadow_report(advice, _shadow_actuals(slow=1.0, henry=20.0), ledger)

    assert shadow["matches_advice"] is False
    assert shadow["recommended_total"] == 20.0
    assert [row["name"] for row in shadow["start"]] == ["Derrick Henry"]
    assert [row["name"] for row in shadow["sit"]] == ["Slow Back"]


def test_shadow_without_calibration_warns_and_stays_on_raw_advice():
    advice, players = _advice_pair()
    ledger = _user_ledger(players, means=None)
    ledger["calibration"] = None

    shadow = shadow_report(advice, _shadow_actuals(), ledger)
    missing = shadow_report(advice, _shadow_actuals(), None)

    assert shadow["model"] == "raw"
    assert shadow["warning"]
    assert shadow["matches_advice"] is True
    assert missing["model"] == "raw"
    assert missing["matches_advice"] is True
    assert missing["warning"]


def test_sealed_body_round_trips_its_priors():
    sealed = seal_calibration_params(
        {
            "schema_version": 1,
            "kind": "calibration_params",
            "season": 2026,
            "week": 1,
            "league_key": YAHOO,
            "pooled": True,
            "fit_weeks": [],
            "n_by_position": {},
            "priors": dict(PRIORS),
            "parameters": {},
        }
    )

    assert parse_calibration_params(sealed)["sha256"] == sealed["sha256"]


def _shrunk_sleeper_weight():
    """Spec weight: shrink each source's precision toward the pooled precision."""
    n = 200
    k = K_W
    mse_sleeper = 4.0
    mse_espn = 10.0
    mse_pooled = 7.0

    def precision(mse):
        return 1.0 / mse

    sleeper = (n * precision(mse_sleeper) + k * precision(mse_pooled)) / (n + k)
    espn = (n * precision(mse_espn) + k * precision(mse_pooled)) / (n + k)
    return sleeper / (sleeper + espn)


def _advice_pair():
    players = [
        {
            "native_id": "55501",
            "native_player_key": "1.p.55501",
            "name": "Slow Back",
            "position": "RB",
            "team": "KCC",
            "eligible_positions": ["RB", "W/R/T"],
            "selected_position": "RB",
            "player_key": "rb-low",
            "projection_key": "rb-low",
            "matched": True,
            "points": 10.0,
            "n": 1,
            "source_points": {"sleeper": 10.0},
        },
        {
            "native_id": "29279",
            "native_player_key": "1.p.29279",
            "name": "Derrick Henry",
            "position": "RB",
            "team": "BAL",
            "eligible_positions": ["RB", "W/R/T"],
            "selected_position": "BN",
            "player_key": "12626",
            "projection_key": "12626",
            "matched": True,
            "points": 8.0,
            "n": 1,
            "source_points": {"sleeper": 8.0},
        },
    ]
    report = compare_lineup(players, {"RB": 1, "BN": 2})
    advice = build_lineup_snapshot(
        season=2024,
        week=1,
        generated_at=STAMP,
        team_key="t.1",
        team_name="Brian's Team",
        roster_slots={"RB": 1, "BN": 2},
        players=players,
        report=report,
    )
    return advice, players


def _user_ledger(players, *, means):
    rows = []
    for player in players:
        native_id = player["native_id"]
        rows.append(
            {
                **player,
                "team_key": "t.1",
                "calibrated_mean": None if means is None else means[native_id],
                "calibrated_sd": None if means is None else 1.0,
            }
        )
    return build_prediction_ledger(
        season=2024,
        week=1,
        league_key="yahoo:1.l.sit",
        user_team_key="t.1",
        generated_at=STAMP,
        scoring_weights={"rec": 1.0},
        projection_sources={},
        players=rows,
        calibration=None if means is None else {"params_key": "calibration/k", "sha256": "abc"},
    )


def _shadow_actuals(*, slow=2.0, henry=9.0):
    return {
        "schema_version": 2,
        "source": "fixture",
        "synced_at": "2026-09-16T16:00:00Z",
        "league": {
            "league_id": "sit-1",
            "league_key": "1.l.sit",
            "name": "Sit Start Mock",
            "season": 2024,
            "week": 1,
            "num_teams": 2,
        },
        "matchups": [
            {
                "matchup_id": "1",
                "week": 1,
                "teams": [
                    {"team_key": "t.1", "points": 10.0},
                    {"team_key": "t.2", "points": 8.0},
                ],
            }
        ],
        "players": [
            {
                "native_id": "55501",
                "native_player_key": "1.p.55501",
                "name": "Slow Back",
                "team_key": "t.1",
                "selected_position": "RB",
                "points": slow,
            },
            {
                "native_id": "29279",
                "native_player_key": "1.p.29279",
                "name": "Derrick Henry",
                "team_key": "t.1",
                "selected_position": "BN",
                "points": henry,
            },
        ],
    }
