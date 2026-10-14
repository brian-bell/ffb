"""Backfilled prior facts for calibration and free-agent grading.

Pure. The CLI passes parsed projections, nflverse actual stat lines, and a
crosswalk. Points are scored here with the caller's ``ScoringConfig``; nothing
is fetched or stored.

Kickoff safety: Sleeper's past-week endpoint returns a real per-player weekly
forecast (fractional counting stats, not a box score). It also returns
``updated_at``, and that stamp is not a publish time. Checked on 2026-10-14,
2024 week 1 Mahomes was rewritten at 2024-09-10T03:45:16Z, after the Thursday
kickoff, and 2025 week 1 was rewritten on 2025-10-06. Those stamps are copied
onto ``projection_updated_at`` and are not ``generated_at``.

Every prior row uses ``outcomes.PRIOR_GENERATED_AT`` (``"prior"``).
``select_scoring_facts`` keeps the row only when that team-week has a kickoff
and leaves ``hours_before_kickoff`` null. The row is a training prior for a
later week, not a timed lineup run. ``fit_calibration`` accepts it only from
an earlier season, or from an earlier week of the target season. A non-prior
row from another season stays out, so a stray fact cannot leak in by season
number alone.
"""

from __future__ import annotations

from typing import Any

from ffb.config import FANTASY_POSITIONS, ScoringConfig
from ffb.identity import canonical_defense_key, canonical_team
from ffb.outcomes import PRIOR_GENERATED_AT
from ffb.scoring import ppr_points


def build_prior_facts(
    projections: list[dict[str, Any]],
    actuals: list[dict[str, Any]],
    identities: list[dict[str, Any]],
    *,
    season: int,
    week: int,
    league_key: str,
    scoring: ScoringConfig,
    as_of: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One fact per projected player, scored for ``league_key``.

    Skill players join the crosswalk on Sleeper or ESPN id, then nflverse on
    ``gsis_id``. An id that maps to two canonical players is omitted. Defenses
    join on ``def:<team>``. A projection with no actual stays in the table
    with a null outcome; the actual is never filled with zero.
    """
    if not isinstance(league_key, str) or not league_key:
        raise ValueError("league_key must be a nonempty string")
    sleeper_ids, espn_ids = _identity_indexes(identities)
    actual_by_gsis, actual_by_defense = _actual_indexes(actuals, week=week)
    grouped: dict[str, dict[str, Any]] = {}
    for row in projections:
        if not isinstance(row, dict):
            continue
        if row.get("season") not in (None, season) or not _same_week(row, week):
            continue
        placed = _place_projection(row, scoring=scoring, sleeper_ids=sleeper_ids, espn_ids=espn_ids)
        if placed is None:
            continue
        key, native_id, player_key, position, team, name, source, points, as_of_id = placed
        entry = grouped.setdefault(
            key,
            {
                "native_id": native_id,
                "projection_key": player_key,
                "position": position,
                "team": team,
                "name": name,
                "source_points": {},
                "sleeper_id": as_of_id,
            },
        )
        if source == "sleeper":
            entry["native_id"] = native_id
            entry["sleeper_id"] = as_of_id
        entry["source_points"].setdefault(source, points)
        if not entry.get("name") and name:
            entry["name"] = name
    facts: list[dict[str, Any]] = []
    stamps = as_of or {}
    for entry in grouped.values():
        sources = entry["source_points"]
        if not sources:
            continue
        consensus = round(sum(sources.values()) / len(sources), 2)
        actual = _lookup_actual(entry, actual_by_gsis, actual_by_defense, sleeper_ids, espn_ids)
        actual_points = None if actual is None else _score_actual(actual, scoring)
        residual = None if actual_points is None else round(actual_points - consensus, 2)
        source_residuals = {}
        if actual_points is not None:
            source_residuals = {
                source: round(actual_points - projected, 2) for source, projected in sources.items()
            }
        position = entry["position"]
        sleeper_id = entry.get("sleeper_id")
        facts.append(
            {
                "league_key": league_key,
                "season": season,
                "week": week,
                "native_id": entry["native_id"],
                "projection_key": entry["projection_key"],
                "position": position,
                "nfl_team": entry["team"],
                "team_key": None,
                "is_user_team": False,
                "name": entry.get("name") or "",
                "eligible_positions": [position],
                "source_points": dict(sources),
                "consensus": consensus,
                "n": len(sources),
                "calibrated_mean": None,
                "calibrated_sd": None,
                "injury_status": None,
                "selected_position_at_advice": None,
                "recommended_slot": None,
                "generated_at": PRIOR_GENERATED_AT,
                "projection_updated_at": (
                    None if sleeper_id is None else stamps.get(str(sleeper_id))
                ),
                "hours_before_kickoff": None,
                "actual_points": actual_points,
                "actual_selected_position": None,
                "actuals_synced_at": None,
                "residual": residual,
                "source_residuals": source_residuals,
                "dnp_proxy": (
                    actual_points is not None and actual_points == 0.0 and position != "DEF"
                ),
                "moved": False,
            }
        )
    facts.sort(key=lambda row: (row["position"], row["name"], row["native_id"]))
    return facts


def grade_free_agents(
    facts: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score waiver targets from prior facts, which are not fantasy-roster rows.

    League actuals omit free agents. A prior fact has ``team_key is None`` and
    an nflverse actual when the player was in the weekly stat file. A fact
    with a fantasy ``team_key`` is a rostered ledger row and is not graded.
    Candidates match ``projection_key``, ``player_key``, or ``native_id``.
    With no candidate list, every free-agent fact in ``facts`` is graded.
    """
    pool = [fact for fact in facts if isinstance(fact, dict) and not fact.get("team_key")]
    by_key: dict[str, dict[str, Any]] = {}
    for fact in pool:
        for key in (fact.get("projection_key"), fact.get("native_id")):
            if key:
                by_key.setdefault(str(key), fact)
    chosen = pool if candidates is None else []
    missing: list[str] = []
    if candidates is not None:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            keys = [
                str(candidate[field])
                for field in ("projection_key", "player_key", "native_id")
                if candidate.get(field)
            ]
            fact = next((by_key[key] for key in keys if key in by_key), None)
            label = str(candidate.get("name") or candidate.get("player_key") or keys or "")
            if fact is None:
                missing.append(label)
                continue
            chosen.append(fact)
    rows = [_grade_row(fact) for fact in chosen]
    rows.sort(key=lambda row: (row["position"] or "", row["name"], row["native_id"]))
    return {
        "rows": rows,
        "missing": missing,
        "by_position": _position_mae(rows),
    }


def _grade_row(fact: dict[str, Any]) -> dict[str, Any]:
    projected = fact.get("consensus")
    actual = fact.get("actual_points")
    return {
        "league_key": fact.get("league_key"),
        "season": fact.get("season"),
        "week": fact.get("week"),
        "projection_key": fact.get("projection_key"),
        "native_id": fact.get("native_id"),
        "name": fact.get("name") or "",
        "position": fact.get("position"),
        "projected": projected,
        "actual": actual,
        "residual": (
            None
            if projected is None or actual is None
            else round(float(actual) - float(projected), 2)
        ),
        "n": fact.get("n") or 0,
    }


def _position_mae(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        if row["projected"] is None or row["actual"] is None:
            continue
        position = str(row.get("position") or "")
        grouped.setdefault(position, []).append(abs(float(row["projected"]) - float(row["actual"])))
    summary = []
    for position in sorted(grouped):
        errors = grouped[position]
        summary.append(
            {
                "position": position,
                "n": len(errors),
                "mae": round(sum(errors) / len(errors), 2),
            }
        )
    return summary


def _identity_indexes(
    identities: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    sleeper: dict[str, dict[str, Any] | None] = {}
    espn: dict[str, dict[str, Any] | None] = {}
    for row in identities:
        if not isinstance(row, dict) or not row.get("player_key") or not row.get("gsis_id"):
            continue
        for column, index in (("sleeper_id", sleeper), ("espn_id", espn)):
            native = row.get(column)
            if not native:
                continue
            key = str(native)
            current = index.get(key, "missing")
            if current == "missing":
                index[key] = row
            elif current is not None and current.get("player_key") != row["player_key"]:
                index[key] = None
    return (
        {key: row for key, row in sleeper.items() if row is not None},
        {key: row for key, row in espn.items() if row is not None},
    )


def _actual_indexes(
    actuals: list[dict[str, Any]], *, week: int
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_gsis: dict[str, dict[str, Any]] = {}
    by_defense: dict[str, dict[str, Any]] = {}
    for row in actuals:
        if not isinstance(row, dict) or row.get("week") != week:
            continue
        if row.get("position") == "DEF":
            native = row.get("native_id")
            if isinstance(native, str) and native.startswith("def:"):
                by_defense[native] = row
            continue
        gsis_id = row.get("gsis_id")
        if isinstance(gsis_id, str) and gsis_id:
            by_gsis[gsis_id] = row
    return by_gsis, by_defense


def _place_projection(
    row: dict[str, Any],
    *,
    scoring: ScoringConfig,
    sleeper_ids: dict[str, dict[str, Any]],
    espn_ids: dict[str, dict[str, Any]],
) -> tuple[str, str, str, str, str | None, str, str, float, str | None] | None:
    position = row.get("position")
    if position not in FANTASY_POSITIONS:
        return None
    source = row.get("source")
    if source not in {"sleeper", "espn"}:
        return None
    stats = row.get("stats")
    if not isinstance(stats, dict) or not stats:
        return None
    if not any(key in scoring.weights for key in stats):
        return None
    native = str(row.get("native_id") or "")
    if not native:
        return None
    as_of_id = native if source == "sleeper" else None
    if position == "DEF":
        defense = canonical_defense_key("DEF", row.get("team"))
        if defense is None:
            return None
        player_key, team = defense
        points = round(ppr_points(stats, scoring, position="DEF"), 2)
        name = row.get("full_name") or team
        return (
            player_key,
            player_key,
            player_key,
            "DEF",
            team,
            str(name),
            source,
            points,
            as_of_id,
        )
    identity_row = sleeper_ids.get(native) if source == "sleeper" else espn_ids.get(native)
    if identity_row is None:
        return None
    team = canonical_team(row.get("team")) or canonical_team(identity_row.get("team"))
    points = round(ppr_points(stats, scoring, position=position), 2)
    player_key = str(identity_row["player_key"])
    return (
        player_key,
        native,
        player_key,
        position,
        team,
        str(row.get("full_name") or ""),
        source,
        points,
        as_of_id,
    )


def _lookup_actual(
    entry: dict[str, Any],
    actual_by_gsis: dict[str, dict[str, Any]],
    actual_by_defense: dict[str, dict[str, Any]],
    sleeper_ids: dict[str, dict[str, Any]],
    espn_ids: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if entry["position"] == "DEF":
        return actual_by_defense.get(entry["projection_key"])
    identity_row = None
    sleeper_id = entry.get("sleeper_id")
    if sleeper_id is not None:
        identity_row = sleeper_ids.get(str(sleeper_id))
    if identity_row is None:
        identity_row = espn_ids.get(str(entry["native_id"]))
    if identity_row is None:
        return None
    gsis_id = identity_row.get("gsis_id")
    if not isinstance(gsis_id, str):
        return None
    return actual_by_gsis.get(gsis_id)


def _score_actual(actual: dict[str, Any], scoring: ScoringConfig) -> float:
    stats = actual.get("stats")
    if not isinstance(stats, dict):
        return 0.0
    return round(ppr_points(stats, scoring, position=actual.get("position")), 2)


def _same_week(row: dict[str, Any], week: int) -> bool:
    scope = row.get("scope")
    if scope is None:
        return True
    return scope == f"week{week}"
