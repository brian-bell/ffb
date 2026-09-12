"""Team-bye store methods: replace-mirror semantics, season-scoped reads."""

import pytest


def _bye_row(team, bye, season=2026):
    return {"season": season, "source": "schedule", "team": team, "bye": bye}


def test_replace_team_byes_mirrors_slice(store):
    store.replace_team_byes([_bye_row("KCC", 10), _bye_row("SFO", 9)], 2026)
    store.replace_team_byes([_bye_row("KCC", 6), _bye_row("PHI", 5)], 2026)
    rows = store.team_bye_rows(2026)
    assert {(r["team"], r["bye"]) for r in rows} == {("KCC", 6), ("PHI", 5)}


def test_replace_team_byes_is_atomic(store):
    store.replace_team_byes([_bye_row("KCC", 10)], 2026)
    with pytest.raises(Exception, match="(?i)constraint|null"):
        store.replace_team_byes([_bye_row("SFO", 9), {"season": 2026}], 2026)
    rows = store.team_bye_rows(2026)
    assert {(r["team"], r["bye"]) for r in rows} == {("KCC", 10)}


def test_team_bye_rows_scoped_by_season(store):
    store.replace_team_byes([_bye_row("KCC", 10, season=2025)], 2025)
    store.replace_team_byes([_bye_row("KCC", 6, season=2026)], 2026)
    assert [r["bye"] for r in store.team_bye_rows(2026)] == [6]
    assert [r["bye"] for r in store.team_bye_rows(2025)] == [10]


def test_source_counts_schedule(store):
    store.replace_team_byes([_bye_row("KCC", 10), _bye_row("SFO", 9)], 2026)
    assert store.source_counts(2026, "schedule") == (2, 2)


def _game_row(week, home, away, season=2026):
    return {
        "season": season,
        "source": "schedule",
        "week": week,
        "home_team": home,
        "away_team": away,
    }


def test_replace_schedule_games_mirrors_slice(store):
    store.replace_schedule_games([_game_row(15, "BAL", "BUF"), _game_row(16, "KCC", "BAL")], 2026)
    store.replace_schedule_games([_game_row(15, "CIN", "CLE")], 2026)
    rows = store.schedule_game_rows(2026)
    assert {(r["week"], r["home_team"], r["away_team"]) for r in rows} == {(15, "CIN", "CLE")}


def test_schedule_game_rows_scoped_by_season(store):
    store.replace_schedule_games([_game_row(15, "BAL", "BUF", season=2025)], 2025)
    store.replace_schedule_games([_game_row(16, "BAL", "CIN", season=2026)], 2026)
    assert [(r["week"], r["away_team"]) for r in store.schedule_game_rows(2026)] == [(16, "CIN")]
    assert [(r["week"], r["away_team"]) for r in store.schedule_game_rows(2025)] == [(15, "BUF")]


def test_source_counts_schedule_stay_on_bye_rows(store):
    store.replace_team_byes([_bye_row("KCC", 10)], 2026)
    store.replace_schedule_games([_game_row(15, "KCC", "BAL"), _game_row(16, "BAL", "CIN")], 2026)
    assert store.source_counts(2026, "schedule") == (1, 1)


def test_replace_schedule_mirrors_byes_and_games(store):
    store.replace_schedule(
        [_bye_row("KCC", 10), _bye_row("SFO", 9)],
        [_game_row(15, "KCC", "BAL"), _game_row(16, "SFO", "BAL")],
        2026,
    )
    store.replace_schedule([_bye_row("PHI", 5)], [_game_row(15, "PHI", "DAL")], 2026)
    assert {(r["team"], r["bye"]) for r in store.team_bye_rows(2026)} == {("PHI", 5)}
    games = {(r["week"], r["home_team"], r["away_team"]) for r in store.schedule_game_rows(2026)}
    assert games == {(15, "PHI", "DAL")}


def test_replace_schedule_rolls_back_byes_and_games_together(store):
    store.replace_schedule([_bye_row("KCC", 10)], [_game_row(15, "KCC", "BAL")], 2026)
    with pytest.raises(Exception, match="(?i)constraint|null"):
        store.replace_schedule(
            [_bye_row("SFO", 9)],
            [_game_row(16, "BAL", "CIN"), {"season": 2026}],
            2026,
        )
    assert {(r["team"], r["bye"]) for r in store.team_bye_rows(2026)} == {("KCC", 10)}
    games = {(r["week"], r["home_team"], r["away_team"]) for r in store.schedule_game_rows(2026)}
    assert games == {(15, "KCC", "BAL")}
