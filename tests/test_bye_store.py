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
