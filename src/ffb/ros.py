"""Rest-of-season strategy: season consensus, playoff slate, bye planning.

Pure compute over consensus rows, stored team byes, and schedule games. The
CLI loads DuckDB; this module never does. Usage-trend stash/buy-low flags are
omitted until nflverse stats ingest exists.
"""

from __future__ import annotations

from typing import Any

from ffb.vorp import BENCH_SLOT

PLAYOFF_WEEKS = (15, 16, 17)
NON_STARTING_SLOTS = frozenset({BENCH_SLOT, "IR", "IL"})


def parse_playoff_weeks(
    spec: str | None, default: tuple[int, ...] = PLAYOFF_WEEKS
) -> tuple[int, ...]:
    """Parse a comma-separated playoff-week list into positive integers."""
    if spec is None or not spec.strip():
        return default
    weeks: list[int] = []
    for part in spec.split(","):
        token = part.strip()
        try:
            week = int(token)
        except ValueError as exc:
            raise ValueError(
                "playoff weeks must be a comma-separated list of positive integers"
            ) from exc
        if week < 1:
            raise ValueError("playoff weeks must be a comma-separated list of positive integers")
        weeks.append(week)
    return tuple(weeks)


def _bye_by_team(byes: list[dict[str, Any]]) -> dict[str, int]:
    return {
        row["team"]: row["bye"] for row in byes if row.get("team") and row.get("bye") is not None
    }


def playoff_slate(
    games: list[dict[str, Any]],
    *,
    weeks: tuple[int, ...],
    byes: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return per-team playoff matchups in week order.

    A team's bye is inserted only when that bye falls inside ``weeks``. Weeks
    with neither a stored game nor a bye are omitted rather than guessed.
    """
    wanted = set(weeks)
    slate: dict[str, dict[int, dict[str, Any]]] = {}

    def _slot(team: str, week: int) -> dict[str, Any]:
        per_week = slate.setdefault(team, {})
        return per_week.setdefault(
            week, {"week": week, "opponent": None, "home": False, "bye": False}
        )

    for game in games:
        week = game.get("week")
        if week not in wanted:
            continue
        home = game.get("home_team")
        away = game.get("away_team")
        if not home or not away:
            continue
        home_row = _slot(home, week)
        home_row.update(opponent=away, home=True, bye=False)
        away_row = _slot(away, week)
        away_row.update(opponent=home, home=False, bye=False)

    for team, bye in _bye_by_team(byes or []).items():
        if bye in wanted:
            row = _slot(team, bye)
            if row["opponent"] is None:
                row["bye"] = True

    return {team: [per_week[week] for week in sorted(per_week)] for team, per_week in slate.items()}


def format_matchups(rows: list[dict[str, Any]]) -> str:
    """Render ``BUF, BYE, @KCC`` from a team's ordered playoff slate."""
    parts: list[str] = []
    for row in rows:
        if row.get("bye"):
            parts.append("BYE")
        elif row.get("opponent"):
            parts.append(row["opponent"] if row.get("home") else f"@{row['opponent']}")
    return ", ".join(parts)


def playoff_strength(
    slate: dict[str, list[dict[str, Any]]],
    def_points: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Score each team's playoff slate by opponent DEF consensus.

    Higher opponent DEF points is a harder offensive matchup. Bye weeks do not
    enter the average. Difficulty ``1`` is the hardest ranked slate.
    """
    strength: dict[str, dict[str, Any]] = {}
    for team, rows in slate.items():
        scored = [
            def_points[row["opponent"]]
            for row in rows
            if not row.get("bye") and row.get("opponent") in def_points
        ]
        games = sum(1 for row in rows if not row.get("bye") and row.get("opponent"))
        strength[team] = {
            "team": team,
            "games": games,
            "avg_opp_def": None if not scored else round(sum(scored) / len(scored), 2),
            "difficulty": None,
            "matchups": format_matchups(rows),
        }
    ranked = sorted(
        (team for team, row in strength.items() if row["avg_opp_def"] is not None),
        key=lambda team: (-strength[team]["avg_opp_def"], team),
    )
    for index, team in enumerate(ranked, start=1):
        strength[team]["difficulty"] = index
    return strength


def defense_points(consensus: list[dict[str, Any]]) -> dict[str, float]:
    """Map canonical team → DEF consensus points."""
    points: dict[str, float] = {}
    for row in consensus:
        if (row.get("position") or "").upper() != "DEF":
            continue
        team = row.get("team")
        if team:
            points[str(team)] = float(row["consensus"])
    return points


def _player_team(row: dict[str, Any]) -> str | None:
    if row.get("nfl_team"):
        return str(row["nfl_team"])
    team = row.get("team")
    return str(team) if team else None


def _is_starter(selected: str | None) -> bool:
    return bool(selected) and selected not in NON_STARTING_SLOTS


def bye_plan(
    roster: list[dict[str, Any]],
    byes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group a roster by bye week and flag two-plus starters at one position."""
    bye_lookup = _bye_by_team(byes)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in roster:
        team = _player_team(row)
        bye = bye_lookup.get(team) if team else None
        if bye is None:
            continue
        name = row.get("full_name") or row.get("name") or ""
        position = row.get("primary_position") or row.get("position")
        grouped.setdefault(bye, []).append(
            {
                "name": name,
                "position": position,
                "team": team,
                "selected_position": row.get("selected_position"),
                "starter": _is_starter(row.get("selected_position")),
            }
        )
    plan: list[dict[str, Any]] = []
    for week in sorted(grouped):
        players = sorted(grouped[week], key=lambda item: item["name"])
        starter_counts: dict[str, int] = {}
        for player in players:
            if player["starter"] and player["position"]:
                starter_counts[player["position"]] = starter_counts.get(player["position"], 0) + 1
        thin = sorted(position for position, count in starter_counts.items() if count >= 2)
        plan.append({"bye": week, "players": players, "thin_positions": thin})
    return plan


def ros_report(
    consensus: list[dict[str, Any]],
    *,
    byes: list[dict[str, Any]],
    games: list[dict[str, Any]],
    playoff_weeks: tuple[int, ...] = PLAYOFF_WEEKS,
    def_points: dict[str, float] | None = None,
    roster: list[dict[str, Any]] | None = None,
    position: str | None = None,
) -> dict[str, Any]:
    """Build the ROS strategy report from already-loaded season inputs."""
    opp_def = defense_points(consensus) if def_points is None else def_points
    slate = playoff_slate(games, weeks=playoff_weeks, byes=byes)
    strength = playoff_strength(slate, opp_def)
    bye_lookup = _bye_by_team(byes)
    wanted = None if position is None else position.upper()
    players: list[dict[str, Any]] = []
    for row in consensus:
        pos = row.get("position")
        if wanted and (pos or "").upper() != wanted:
            continue
        team = row.get("team")
        team_slate = slate.get(team or "", [])
        team_strength = strength.get(team or "", {})
        players.append(
            {
                "player_key": row.get("player_key"),
                "name": row.get("full_name") or row.get("name") or "",
                "position": pos,
                "team": team,
                "matched": bool(row.get("matched")),
                "ros": row.get("consensus"),
                "n": row.get("n", 0),
                "bye": bye_lookup.get(team) if team else None,
                "playoff": format_matchups(team_slate),
                "playoff_difficulty": team_strength.get("difficulty"),
                "avg_opp_def": team_strength.get("avg_opp_def"),
            }
        )
    players.sort(key=lambda item: (-(item["ros"] or 0.0), item["name"]))
    for index, row in enumerate(players, start=1):
        row["rank"] = index
    teams = sorted(
        strength.values(),
        key=lambda item: (
            item["difficulty"] is None,
            item["difficulty"] if item["difficulty"] is not None else 0,
            item["team"],
        ),
    )
    return {
        "playoff_weeks": playoff_weeks,
        "players": players,
        "teams": teams,
        "bye_plan": bye_plan(roster, byes) if roster else [],
        "usage_available": False,
    }
