"""Backfilled priors: scoring, kickoff safety, calibration, and free-agent grading."""

from ffb.backtest import walk_forward_backtest
from ffb.calibration import _decision_rows, fit_calibration
from ffb.cli import app
from ffb.config import LEAGUE_SCORING, ScoringConfig
from ffb.outcomes import PRIOR_GENERATED_AT, select_scoring_facts
from ffb.prior_data import load_prior_facts, sync_priors
from ffb.priors import build_prior_facts, grade_free_agents
from ffb.scoring import ppr_points
from ffb.snapshot import SnapshotCache, SnapshotPolicy
from ffb.sources.player_stats import parse_actuals, points_allowed_band
from ffb.sources.sleeper import projection_as_of

from .cli_plain import PlainCliRunner

runner = PlainCliRunner()

SLEEPER_SCORING = ScoringConfig(
    weights={
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -1.0,
        "pass_2pt": 2.0,
        "fum_lost": -2.0,
        "fum_rec_td": 6.0,
        "def_fum_td": 6.0,
    }
)
HALF = ScoringConfig(weights={"rec": 0.5, "rec_yd": 0.1, "sack": 1.0, "pts_allow_14_20": 1.0})
FULL = ScoringConfig(weights={"rec": 1.0, "rec_yd": 0.1, "sack": 1.0, "pts_allow_14_20": 1.0})
YAHOO = "yahoo:470.l.928421"
SLEEPER = "sleeper:1395854363380965376"
KICK = "2024-09-06T00:20:00Z"
AFTER = "2024-09-10T03:45:16Z"


def _projection(**overrides):
    row = {
        "native_id": "4046",
        "full_name": "Pat Mahomes",
        "position": "WR",
        "team": "KC",
        "season": 2024,
        "source": "sleeper",
        "scope": "week1",
        "stats": {"rec": 4.0, "rec_yd": 40.0},
    }
    row.update(overrides)
    return row


def _identity(**overrides):
    row = {
        "player_key": "mfl-1",
        "sleeper_id": "4046",
        "espn_id": "15",
        "gsis_id": "00-0033873",
        "team": "KC",
    }
    row.update(overrides)
    return row


def _actual(**overrides):
    row = {
        "gsis_id": "00-0033873",
        "native_id": None,
        "name": "Pat Mahomes",
        "position": "WR",
        "team": "KC",
        "week": 1,
        "stats": {"rec": 4.0, "rec_yd": 30.0},
    }
    row.update(overrides)
    return row


def test_projection_as_of_is_utc_and_not_a_ledger_time():
    raw = [{"player_id": "4046", "company": "rotowire", "updated_at": 1725939916284}]
    assert projection_as_of(raw) == {"4046": AFTER}


def test_priors_are_scored_per_league_and_do_not_invent_a_kickoff_time():
    actual = _actual()
    identity = _identity()
    projection = _projection()
    yahoo = build_prior_facts(
        [projection],
        [actual],
        [identity],
        season=2024,
        week=1,
        league_key=YAHOO,
        scoring=HALF,
        as_of={"4046": AFTER},
    )
    sleeper = build_prior_facts(
        [projection],
        [actual],
        [identity],
        season=2024,
        week=1,
        league_key=SLEEPER,
        scoring=FULL,
        as_of={"4046": AFTER},
    )
    assert len(yahoo) == 1
    assert yahoo[0]["league_key"] == YAHOO
    assert sleeper[0]["league_key"] == SLEEPER
    # 4 receptions and 40 yards: half-PPR 6.0, full-PPR 8.0. Actual yards are 30.
    assert yahoo[0]["consensus"] == 6.0
    assert sleeper[0]["consensus"] == 8.0
    assert yahoo[0]["actual_points"] == 5.0
    assert sleeper[0]["actual_points"] == 7.0
    assert yahoo[0]["generated_at"] == PRIOR_GENERATED_AT
    assert yahoo[0]["projection_updated_at"] == AFTER
    assert yahoo[0]["hours_before_kickoff"] is None
    assert yahoo[0]["team_key"] is None
    assert yahoo[0]["is_user_team"] is False
    assert yahoo[0]["source_points"] == {"sleeper": 6.0}


def test_a_missing_actual_stays_null_and_an_ambiguous_id_is_not_joined():
    missing = build_prior_facts(
        [_projection()],
        [],
        [_identity()],
        season=2024,
        week=1,
        league_key=YAHOO,
        scoring=HALF,
    )
    assert missing[0]["actual_points"] is None
    ambiguous = build_prior_facts(
        [_projection()],
        [_actual()],
        [_identity(), _identity(player_key="mfl-2", gsis_id="00-other")],
        season=2024,
        week=1,
        league_key=YAHOO,
        scoring=HALF,
    )
    assert ambiguous == []


def test_espn_and_sleeper_share_one_prior_row():
    facts = build_prior_facts(
        [
            _projection(),
            _projection(native_id="15", source="espn", stats={"rec": 6.0, "rec_yd": 40.0}),
        ],
        [_actual()],
        [_identity()],
        season=2024,
        week=1,
        league_key=YAHOO,
        scoring=HALF,
    )
    assert len(facts) == 1
    assert facts[0]["native_id"] == "4046"
    assert facts[0]["source_points"] == {"sleeper": 6.0, "espn": 7.0}
    assert facts[0]["consensus"] == 6.5
    assert facts[0]["n"] == 2


def test_defense_priors_use_the_canonical_team_id():
    facts = build_prior_facts(
        [
            _projection(
                native_id="DEF-KC",
                full_name="Kansas City",
                position="DEF",
                team="KC",
                stats={"sack": 2.0, "pts_allow_14_20": 1.0},
            )
        ],
        [
            {
                "gsis_id": None,
                "native_id": "def:KCC",
                "name": "KCC",
                "position": "DEF",
                "team": "KCC",
                "week": 1,
                "stats": {"sack": 3.0, "pts_allow_14_20": 1.0},
            }
        ],
        [],
        season=2024,
        week=1,
        league_key=YAHOO,
        scoring=HALF,
    )
    assert facts[0]["native_id"] == "def:KCC"
    assert facts[0]["consensus"] == 3.0
    assert facts[0]["actual_points"] == 4.0


def test_prior_rows_need_a_kickoff_and_do_not_replace_a_ledger_player():
    prior = build_prior_facts(
        [_projection(), _projection(native_id="99", full_name="Free Agent")],
        [_actual(), _actual(gsis_id="00-fa")],
        [
            _identity(),
            _identity(player_key="mfl-fa", sleeper_id="99", espn_id="99", gsis_id="00-fa"),
        ],
        season=2024,
        week=1,
        league_key=YAHOO,
        scoring=HALF,
    )
    assert {row["native_id"] for row in prior} == {"4046", "99"}
    ledger = {
        **prior[0],
        "native_id": "4046",
        "generated_at": "2024-09-05T12:00:00Z",
        "consensus": 1.0,
        "source_points": {"sleeper": 1.0},
        "team_key": "t.1",
        "is_user_team": True,
        "hours_before_kickoff": None,
    }
    chosen = select_scoring_facts([ledger, *prior], {(1, "KC"): KICK})
    by_id = {row["native_id"]: row for row in chosen}
    assert by_id["4046"]["generated_at"] == "2024-09-05T12:00:00Z"
    assert by_id["4046"]["consensus"] == 1.0
    assert by_id["4046"]["hours_before_kickoff"] is not None
    assert by_id["99"]["generated_at"] == PRIOR_GENERATED_AT
    assert by_id["99"]["hours_before_kickoff"] is None
    assert by_id["99"]["team_key"] is None
    assert select_scoring_facts(prior, {}) == []


def test_fit_uses_earlier_season_priors_and_ignores_a_non_prior_other_season():
    prior = build_prior_facts(
        [_projection(scope="week18")],
        [_actual(week=18)],
        [_identity()],
        season=2024,
        week=18,
        league_key=YAHOO,
        scoring=HALF,
    )
    current = {
        **prior[0],
        "season": 2026,
        "week": 1,
        "generated_at": "2026-09-08T12:00:00Z",
        "consensus": 10.0,
        "actual_points": 10.0,
        "source_points": {"sleeper": 10.0},
    }
    poison = {
        **current,
        "season": 2025,
        "native_id": "poison",
        "actual_points": 10000.0,
        "generated_at": "2025-10-01T00:00:00Z",
    }
    same_player_prior = {**current, "generated_at": PRIOR_GENERATED_AT, "consensus": 99.0}
    rows = _decision_rows([*prior, current, poison, same_player_prior], season=2026, week=3)
    by_key = {(row["season"], row["week"], row["native_id"]): row for row in rows}
    assert by_key[(2026, 1, "4046")]["consensus"] == 10.0
    assert by_key[(2024, 18, "4046")]["season"] == 2024
    assert "poison" not in {row["native_id"] for row in rows}
    params = fit_calibration(
        [*prior, current, poison, same_player_prior],
        season=2026,
        week=3,
        league_key=YAHOO,
    )
    assert params["fit_weeks"] == [1]
    assert params["n_by_position"]["WR"] == 2


def test_walk_forward_does_not_borrow_another_seasons_kickoff():
    prior = {
        "league_key": YAHOO,
        "season": 2024,
        "week": 1,
        "native_id": "bye",
        "position": "RB",
        "nfl_team": "DAL",
        "team_key": None,
        "is_user_team": False,
        "name": "Bye",
        "eligible_positions": ["RB"],
        "source_points": {"sleeper": 10.0},
        "consensus": 10.0,
        "n": 1,
        "actual_points": 50.0,
        "generated_at": PRIOR_GENERATED_AT,
    }
    kept = {
        **prior,
        "week": 18,
        "native_id": "kept",
        "nfl_team": "KC",
        "actual_points": 12.0,
    }
    ledger = {
        **kept,
        "season": 2026,
        "week": 3,
        "native_id": "now",
        "nfl_team": "KC",
        "generated_at": "2026-09-20T12:00:00Z",
        "consensus": 10.0,
        "actual_points": 10.0,
        "is_user_team": True,
        "team_key": "t.1",
        "recommended_slot": "RB",
        "selected_position_at_advice": "RB",
    }
    report = walk_forward_backtest(
        [prior, kept, ledger],
        season=2026,
        kickoffs_by_season={
            2026: {(3, "KC"): "2026-09-21T17:00:00Z", (1, "DAL"): "2026-09-08T17:00:00Z"},
            2024: {(18, "KC"): "2025-01-05T18:00:00Z"},
        },
    )
    assert [row["week"] for row in report["weeks"]] == [3]
    assert report["weeks"][0]["fit_weeks"] == []
    assert report["weeks"][0]["n_by_position"] == {"RB": 1}
    assert report["prior_rows"] == 1


def test_free_agent_grade_skips_rostered_rows_and_reports_misses():
    facts = build_prior_facts(
        [_projection(native_id="99", full_name="Free Agent")],
        [_actual(gsis_id="00-fa")],
        [_identity(player_key="mfl-fa", sleeper_id="99", espn_id="99", gsis_id="00-fa")],
        season=2024,
        week=1,
        league_key=SLEEPER,
        scoring=HALF,
    )
    rostered = {**facts[0], "native_id": "rostered", "team_key": "t.1", "projection_key": "mfl-ros"}
    report = grade_free_agents(
        [*facts, rostered],
        candidates=[
            {"player_key": "mfl-fa", "name": "Free Agent"},
            {"player_key": "mfl-ros", "name": "Rostered"},
            {"player_key": "missing", "name": "Nobody"},
        ],
    )
    assert [row["name"] for row in report["rows"]] == ["Free Agent"]
    assert report["rows"][0]["actual"] == 5.0
    assert report["missing"] == ["Rostered", "Nobody"]
    assert report["by_position"][0]["position"] == "WR"
    assert report["by_position"][0]["n"] == 1


def test_player_stats_score_skill_players_and_points_allowed_without_inventing_a_shutout():
    raw = {
        "players": [
            {
                "player_id": "00-1",
                "player_display_name": "Pat",
                "position": "WR",
                "season": 2024,
                "week": 1,
                "season_type": "REG",
                "team": "KC",
                "receptions": 4,
                "receiving_yards": 30,
                "special_teams_tds": 1,
            },
            {
                "player_id": "00-2",
                "player_display_name": "Edge",
                "position": "LB",
                "season": 2024,
                "week": 1,
                "season_type": "REG",
                "team": "KC",
                "def_sacks": 1,
                "def_tds": 1,
            },
            {
                "player_id": "00-3",
                "player_display_name": "Preseason",
                "position": "WR",
                "season": 2024,
                "week": 1,
                "season_type": "PRE",
                "team": "KC",
                "receptions": 9,
            },
        ],
        "games": [
            {
                "season": 2024,
                "game_type": "REG",
                "week": 1,
                "home_team": "KC",
                "away_team": "BAL",
                "home_score": 27,
                "away_score": 20,
            },
            {
                "season": 2024,
                "game_type": "REG",
                "week": 2,
                "home_team": "DAL",
                "away_team": "NYG",
                "home_score": None,
                "away_score": None,
            },
        ],
    }
    rows = parse_actuals(raw, 2024)
    skill = next(row for row in rows if row["gsis_id"] == "00-1")
    assert skill["stats"] == {"rec": 4.0, "rec_yd": 30.0}
    assert "def_ret_td" not in skill["stats"]
    defense = next(row for row in rows if row["native_id"] == "def:KCC")
    assert defense["stats"]["sack"] == 1.0
    assert defense["stats"]["pass_int_td"] == 1.0
    assert defense["stats"]["def_ret_td"] == 1.0
    assert defense["stats"]["pts_allow_14_20"] == 1.0
    baltimore = next(row for row in rows if row["native_id"] == "def:BAL")
    assert baltimore["stats"]["pts_allow_21_27"] == 1.0
    assert all(row["team"] != "DAL" for row in rows)
    assert points_allowed_band(0) == "pts_allow_0"
    assert points_allowed_band(35) == "pts_allow_35p"


def test_player_stats_score_turnovers_with_yahoo_and_sleeper_weights():
    raw = {
        "players": [
            {
                "player_id": "00-qb",
                "player_display_name": "QB",
                "position": "QB",
                "season": 2024,
                "week": 1,
                "season_type": "REG",
                "team": "KC",
                "passing_yards": 100,
                "passing_tds": 1,
                "passing_interceptions": 1,
                "sack_fumbles_lost": 1,
                "rushing_fumbles_lost": 1,
                "receiving_fumbles_lost": 0,
                "passing_2pt_conversions": 1,
                "fumble_recovery_tds": 1,
            },
            {
                "player_id": "00-lb",
                "player_display_name": "LB",
                "position": "LB",
                "season": 2024,
                "week": 1,
                "season_type": "REG",
                "team": "KC",
                "fumble_recovery_tds": 1,
            },
        ],
        "games": [],
    }
    rows = parse_actuals(raw, 2024)
    quarterback = next(row for row in rows if row["gsis_id"] == "00-qb")
    assert quarterback["stats"]["pass_int"] == 1.0
    assert quarterback["stats"]["fum_lost"] == 2.0
    assert quarterback["stats"]["pass_2pt"] == 1.0
    assert quarterback["stats"]["fum_rec_td"] == 1.0
    # Yahoo has no turnover weights, so the interception and lost fumbles stay
    # on the line and do not change the total. 100 yards, a pass TD, a two-point
    # conversion, and a fumble-recovery TD.
    assert ppr_points(quarterback["stats"], LEAGUE_SCORING, position="QB") == 17.0
    assert ppr_points(quarterback["stats"], SLEEPER_SCORING, position="QB") == 11.0
    assert all(row.get("gsis_id") != "00-lb" for row in rows)
    defense = next(row for row in rows if row["native_id"] == "def:KCC")
    assert defense["stats"]["def_fum_td"] == 1.0
    assert ppr_points(defense["stats"], LEAGUE_SCORING, position="DEF") == 6.0


def test_loader_reads_snapshots_and_offline_sync_does_not_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ffb.prior_data.sleeper.fetch_projections",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    cache = SnapshotCache(tmp_path)
    cache.put_json(
        "nflverse/ff_playerids",
        [
            {
                "mfl_id": "mfl-1",
                "sleeper_id": "4046",
                "espn_id": "15",
                "gsis_id": "00-0033873",
                "name": "Pat",
                "position": "WR",
                "team": "KC",
            }
        ],
    )
    cache.put_json(
        "nflverse/player_stats_2024",
        {
            "players": [
                {
                    "player_id": "00-0033873",
                    "player_display_name": "Pat",
                    "position": "WR",
                    "season": 2024,
                    "week": 1,
                    "season_type": "REG",
                    "team": "KC",
                    "receptions": 4,
                    "receiving_yards": 30,
                }
            ],
            "games": [],
        },
    )
    cache.put_json(
        "sleeper/projections_nfl_2024_regular_week1",
        [
            {
                "player_id": "4046",
                "company": "rotowire",
                "season": 2024,
                "week": 1,
                "updated_at": 1725939916284,
                "player": {
                    "first_name": "Pat",
                    "last_name": "Mahomes",
                    "position": "WR",
                    "team": "KC",
                },
                "stats": {"rec": 4, "rec_yd": 40, "pts_ppr": 9},
            }
        ],
    )
    facts, notes = load_prior_facts(
        cache, seasons=[2024], scoring_by_league={YAHOO: HALF, SLEEPER: FULL}
    )
    assert notes == []
    assert len(facts) == 2
    assert {fact["league_key"] for fact in facts} == {YAHOO, SLEEPER}
    assert {fact["consensus"] for fact in facts} == {6.0, 8.0}
    assert all(fact["projection_updated_at"] == AFTER for fact in facts)
    misses = sync_priors(cache, [2025], policy=SnapshotPolicy.OFFLINE)
    assert misses
    assert any("offline snapshot missing" in note for note in misses)


def test_refresh_keeps_a_snapshot_the_parser_cannot_use(tmp_path, monkeypatch):
    cache = SnapshotCache(tmp_path)
    key = "sleeper/projections_nfl_2024_regular_week1"
    good = [
        {
            "player_id": "4046",
            "company": "rotowire",
            "player": {"position": "WR", "team": "KC", "first_name": "Pat", "last_name": "M"},
            "stats": {"rec": 4, "rec_yd": 40},
        }
    ]
    cache.put_json(key, good)

    def _unusable(*_args, **_kwargs):
        return [{"company": "rotowire", "player_id": "4046"}]

    monkeypatch.setattr("ffb.prior_data.sleeper.fetch_projections", _unusable)
    monkeypatch.setattr("ffb.prior_data.espn.fetch_projections", lambda *_a, **_k: [{"id": 1}])
    monkeypatch.setattr(
        "ffb.prior_data.schedule.fetch_schedule", lambda *_a, **_k: [{"nope": True}]
    )
    monkeypatch.setattr(
        "ffb.prior_data.crosswalk.fetch_playerids", lambda *_a, **_k: [{"nope": True}]
    )
    monkeypatch.setattr(
        "ffb.prior_data.player_stats.fetch_player_stats",
        lambda *_a, **_k: {"players": [{}], "games": []},
    )

    notes = sync_priors(cache, [2024], policy=SnapshotPolicy.REFRESH)
    assert cache.read_json(key) == good
    assert f"{key} was rejected; kept the existing snapshot" in notes
    assert "espn/projections_2024_week1 was rejected and not snapshotted" in notes
    assert not cache.has("espn/projections_2024_week1")


def test_priors_cli_replays_snapshots_and_waivers_stay_on_sleeper(tmp_path, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("network")

    for name in (
        "sleeper.fetch_projections",
        "espn.fetch_projections",
        "player_stats.fetch_player_stats",
        "schedule.fetch_schedule",
        "crosswalk.fetch_playerids",
    ):
        monkeypatch.setattr(f"ffb.prior_data.{name}", _boom)
    cache = SnapshotCache(tmp_path / "snapshots")
    cache.put_json(
        "nflverse/ff_playerids",
        [
            {
                "mfl_id": "mfl-1",
                "sleeper_id": "4046",
                "espn_id": "15",
                "gsis_id": "00-0033873",
                "name": "Pat",
                "position": "WR",
                "team": "KC",
            }
        ],
    )
    cache.put_json("nflverse/schedule_2024", [{"season": 2024, "week": 1}])
    cache.put_json(
        "nflverse/player_stats_2024",
        {
            "players": [
                {
                    "player_id": "00-0033873",
                    "player_display_name": "Pat",
                    "position": "WR",
                    "season": 2024,
                    "week": 1,
                    "season_type": "REG",
                    "team": "KC",
                    "receptions": 4,
                    "receiving_yards": 30,
                }
            ],
            "games": [],
        },
    )
    sleeper_row = [
        {
            "player_id": "4046",
            "company": "rotowire",
            "season": 2024,
            "week": 1,
            "updated_at": 1725939916284,
            "player": {
                "first_name": "Pat",
                "last_name": "Mahomes",
                "position": "WR",
                "team": "KC",
            },
            "stats": {"rec": 4, "rec_yd": 40},
        }
    ]
    for week in range(1, 19):
        cache.put_json(f"sleeper/projections_nfl_2024_regular_week{week}", sleeper_row)
        cache.put_json(f"espn/projections_2024_week{week}", [{}])
    env = {
        "FFB_DB_PATH": str(tmp_path / "ffb.duckdb"),
        "FFB_SNAPSHOT_DIR": str(tmp_path / "snapshots"),
    }

    built = runner.invoke(app, ["calibrate", "priors", "--season", "2024", "--offline"], env=env)
    assert built.exit_code == 0, built.output
    assert f"2024 week 1 {YAHOO}:" in built.output
    assert SLEEPER not in built.output

    both = runner.invoke(app, ["calibrate", "priors", "--offline", "--refresh"], env=env)
    assert both.exit_code != 0

    yahoo = runner.invoke(
        app, ["calibrate", "waivers", "2024", "--week", "1", "--league", "yahoo"], env=env
    )
    assert yahoo.exit_code == 1
    assert "Sleeper leagues only" in yahoo.output

    missing = runner.invoke(app, ["calibrate", "waivers", "2024", "--week", "1"], env=env)
    assert missing.exit_code == 1
    assert "ffb league sync 2024 --league sleeper" in missing.output
