"""Join prediction ledgers to weekly actuals and score sit/start decisions.

Pure compute. The CLI walks snapshots and passes parsed ledgers, actuals, and
lineup advice in. Identity is ``native_id``, with Yahoo team-defense ids
(``100014``) and ``def:<team>`` treated as the same defense. Names are never a
join key.

``calibrated_mean`` / ``calibrated_sd`` are copied from the ledger when a v2
run stored them, and stay ``None`` on v1. Kickoff times are not on the ledger
or the actuals bundle. Callers pass them from the nflverse schedule snapshot
(``gameday`` + ``gametime``, US Eastern). ``select_scoring_facts`` is the one
roster rule shared with the walk-forward backtest.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ffb.actuals import WeeklyActualsBundle, parse_actuals
from ffb.config import namespaced_league_key
from ffb.identity import (
    canonical_team,
    defense_identity,
    is_defense_position,
    yahoo_defense_identity,
)
from ffb.lineup import (
    BENCH_SLOT,
    NON_STARTING_SLOTS,
    UNAVAILABLE_STATUSES,
    can_fill,
    is_starter,
    optimal_starters,
)
from ffb.predlog import parse_prediction_ledger
from ffb.retro import parse_lineup_snapshot

_UNSTARTABLE_SLOTS = NON_STARTING_SLOTS - {BENCH_SLOT}


def fact_table(
    ledger: dict[str, Any],
    actuals: WeeklyActualsBundle | dict[str, Any] | None = None,
    advice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One fact row per ledger player, plus the anti-join as ``missing_actuals``.

    Grain is ``(league_key, season, week, native_id, generated_at)``. A player
    whose actuals ``team_key`` differs from the ledger's is kept and tagged
    ``moved``. Rows with no actuals stay in ``facts`` with null outcomes and
    are listed in ``missing_actuals``.
    """
    parsed = parse_prediction_ledger(ledger)
    bundle = _bundle(actuals, season=parsed["season"])
    if bundle is not None:
        _require_same_week(parsed, bundle)
    snapshot = parse_lineup_snapshot(advice) if advice is not None else None
    league_key = parsed["league_key"]
    actual_rows = [] if bundle is None else bundle.players
    index, exact = _index_actuals(actual_rows, league_key)
    slots = _recommended_slots(snapshot, league_key)
    advice_team = None if snapshot is None else snapshot["team_key"]
    synced_at = None if bundle is None else bundle.data["synced_at"]

    facts: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for player in parsed["players"]:
        row = _fact_row(
            parsed,
            player,
            index=index,
            exact=exact,
            slots=slots,
            advice_team=advice_team,
            synced_at=synced_at,
        )
        facts.append(row)
        if row["actual_points"] is None:
            missing.append({"native_id": row["native_id"], "name": row["name"]})
    return {"facts": facts, "missing_actuals": missing}


def decision_table(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """User-team starter versus each eligible bench player, one row per pair.

    ``projected_gap`` and ``realized_gap`` are starter minus bench. ``correct``
    is sign equality of those gaps, or ``None`` when either gap is missing.
    ``dnp_proxy`` is true when either player is a zero-point non-defense.
    """
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for fact in facts:
        key = (fact["league_key"], fact["season"], fact["week"], fact["generated_at"])
        grouped.setdefault(key, []).append(fact)

    rows: list[dict[str, Any]] = []
    for group in grouped.values():
        starters = [
            fact
            for fact in group
            if fact.get("is_user_team") and is_starter(fact.get("recommended_slot"))
        ]
        bench = [
            fact
            for fact in group
            if fact.get("is_user_team")
            and fact.get("recommended_slot") == BENCH_SLOT
            and not _unavailable(fact)
        ]
        for starter in starters:
            slot = starter["recommended_slot"]
            for other in bench:
                if other["native_id"] == starter["native_id"]:
                    continue
                if not can_fill(other, slot):
                    continue
                rows.append(_decision_row(starter, other, slot))
    rows.sort(
        key=lambda row: (
            row["league_key"],
            row["season"],
            row["week"],
            row["generated_at"],
            row["slot"],
            row["starter_native_id"],
            row["bench_native_id"],
        )
    )
    return rows


def history_report(
    facts: list[dict[str, Any]],
    *,
    season: int,
    roster_slots: dict[tuple[str, int], dict[str, int]] | None = None,
    kickoffs: Mapping[tuple[int, str], str] | None = None,
) -> dict[str, Any]:
    """Cross-week metrics on each league-week's pre-kickoff roster.

    Source accuracy uses :func:`select_scoring_facts`. Decision metrics (hit
    rate, Questionable hit rate, regret) use that roster only when every
    scored user row comes from the roster run and that run has an exact-time
    advice snapshot. Source accuracy bias matches weekly retro:
    mean(projected − actual). Each accuracy row and the hit rates are reported
    with the DNP proxy rows included and again without them.
    """
    chosen = select_scoring_facts(facts, kickoffs)
    slots = roster_slots or {}
    decision_facts, notes = _decision_facts(chosen, slots)
    if kickoffs is not None and len(kickoffs) == 0:
        notes.insert(0, NOTE_NO_KICKOFFS)
    decisions = decision_table(decision_facts)
    return {
        "season": season,
        "leagues": sorted({fact["league_key"] for fact in chosen}),
        "notes": notes,
        "source_position_accuracy": _source_position_accuracy(chosen),
        "regret_trend": _regret_trend(decision_facts, slots),
        "q_player_hit_rate": _hit_rate(
            [
                row
                for row in decisions
                if str(row.get("starter_injury_status") or "").upper() == "QUESTIONABLE"
            ]
        ),
        "decision_accuracy": _hit_rate(decisions),
        "missing_actuals": [
            {
                "league_key": fact["league_key"],
                "season": fact["season"],
                "week": fact["week"],
                "generated_at": fact["generated_at"],
                "native_id": fact["native_id"],
                "name": fact.get("name") or "",
            }
            for fact in chosen
            if fact.get("actual_points") is None
        ],
        "weeks_without_actuals": _weeks_without_actuals(chosen),
    }


def _bundle(actuals: WeeklyActualsBundle | dict[str, Any] | None, *, season: int):
    if actuals is None:
        return None
    if isinstance(actuals, WeeklyActualsBundle):
        return actuals
    return parse_actuals(actuals, season=season)


def _require_same_week(ledger: dict[str, Any], bundle: WeeklyActualsBundle) -> None:
    league = bundle.league
    if league["season"] != ledger["season"] or league["week"] != ledger["week"]:
        raise ValueError(
            f"actuals {league['season']} week {league['week']} does not match "
            f"ledger {ledger['season']} week {ledger['week']}"
        )
    actual_league = namespaced_league_key(bundle.data["source"], league["league_key"])
    if actual_league != ledger["league_key"]:
        raise ValueError(
            f"actuals league {actual_league} does not match ledger league {ledger['league_key']}"
        )


def _fact_row(
    ledger: dict[str, Any],
    player: dict[str, Any],
    *,
    index: dict[str, dict[str, Any]],
    exact: dict[str, dict[str, Any]],
    slots: dict[str, str],
    advice_team: str | None,
    synced_at: str | None,
) -> dict[str, Any]:
    league_key = ledger["league_key"]
    raw_id = str(player.get("native_id") or "")
    keys = _join_keys(
        raw_id,
        league_key=league_key,
        position=player.get("position"),
        projection_key=player.get("projection_key"),
        player_key=player.get("player_key"),
        selected_position=player.get("selected_position"),
        nfl_team=player.get("nfl_team"),
    )
    actual = _match_actual(raw_id, keys, index, exact)
    native_id = _canonical_id(raw_id, keys)
    position = player.get("position")
    nfl_team = player.get("nfl_team")
    if native_id.startswith("def:"):
        position = "DEF"
        nfl_team = native_id.removeprefix("def:")
    actual_points = None if actual is None else actual["points"]
    consensus = player.get("consensus")
    residual = (
        None
        if actual_points is None or consensus is None
        else round(float(actual_points) - float(consensus), 2)
    )
    source_points = dict(player.get("source_points") or {})
    source_residuals = {}
    if actual_points is not None:
        for source, projected in source_points.items():
            if projected is None:
                continue
            source_residuals[source] = round(float(actual_points) - float(projected), 2)
    moved = actual is not None and str(actual.get("team_key") or "") != str(
        player.get("team_key") or ""
    )
    return {
        "league_key": league_key,
        "season": ledger["season"],
        "week": ledger["week"],
        "native_id": native_id,
        "projection_key": player.get("projection_key"),
        "position": position,
        "nfl_team": nfl_team,
        "team_key": player.get("team_key"),
        "is_user_team": bool(player.get("is_user_team")),
        "name": player.get("name") or "",
        "eligible_positions": list(player.get("eligible_positions") or []),
        "source_points": source_points,
        "consensus": consensus,
        "n": player.get("n", 0),
        "calibrated_mean": player.get("calibrated_mean"),
        "calibrated_sd": player.get("calibrated_sd"),
        "injury_status": player.get("injury_status"),
        "selected_position_at_advice": player.get("selected_position"),
        "recommended_slot": _recommended_slot(player, native_id, raw_id, slots, advice_team),
        "generated_at": ledger["generated_at"],
        "hours_before_kickoff": None,
        "actual_points": actual_points,
        "actual_selected_position": None if actual is None else actual.get("selected_position"),
        "actuals_synced_at": None if actual is None else synced_at,
        "residual": residual,
        "source_residuals": source_residuals,
        "dnp_proxy": (
            actual_points is not None and float(actual_points) == 0.0 and position != "DEF"
        ),
        "moved": moved,
    }


def _recommended_slot(
    player: dict[str, Any],
    native_id: str,
    raw_id: str,
    slots: dict[str, str],
    advice_team: str | None,
) -> str | None:
    if not player.get("is_user_team") or advice_team is None:
        return None
    if str(player.get("team_key") or "") != advice_team:
        return None
    chosen = slots.get(raw_id)
    if chosen is None:
        chosen = slots.get(native_id)
    if chosen is not None:
        return chosen
    selected = player.get("selected_position")
    if selected in _UNSTARTABLE_SLOTS:
        return selected
    return BENCH_SLOT


def _recommended_slots(advice: dict[str, Any] | None, league_key: str) -> dict[str, str]:
    if advice is None:
        return {}
    slots: dict[str, str] = {}
    for row in advice["report"]["optimal"]:
        raw = str(row.get("native_id") or "")
        slot = row.get("slot") or row.get("selected_position")
        if not raw or not slot:
            continue
        for key in _join_keys(raw, league_key=league_key, selected_position=slot):
            slots.setdefault(key, slot)
        slots[raw] = slot
    return slots


def _decision_row(starter: dict[str, Any], bench: dict[str, Any], slot: str) -> dict[str, Any]:
    projected = _gap(starter.get("consensus"), bench.get("consensus"))
    realized = _gap(starter.get("actual_points"), bench.get("actual_points"))
    return {
        "league_key": starter["league_key"],
        "season": starter["season"],
        "week": starter["week"],
        "generated_at": starter["generated_at"],
        "slot": slot,
        "starter_native_id": starter["native_id"],
        "bench_native_id": bench["native_id"],
        "starter_injury_status": starter.get("injury_status"),
        "projected_gap": projected,
        "realized_gap": realized,
        "correct": (
            None if projected is None or realized is None else _sign(projected) == _sign(realized)
        ),
        "dnp_proxy": bool(starter.get("dnp_proxy") or bench.get("dnp_proxy")),
    }


def _gap(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return round(float(left) - float(right), 2)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _unavailable(fact: dict[str, Any]) -> bool:
    return str(fact.get("injury_status") or "").upper() in UNAVAILABLE_STATUSES


NOTE_NO_KICKOFFS = "no kickoff times available; predictions were not scored"
NOTE_UNPAIRED = "no decision metrics: advice snapshot is not paired to the scored ledger"
NOTE_MIXED = (
    "no decision metrics or league Brier: scored predictions do not share the advice ledger"
)


def uses_one_advice_ledger(facts: list[dict[str, Any]]) -> bool:
    """True when every user row was predicted by the roster run.

    A Thursday row taken from Wednesday and a Sunday row taken from Sunday do
    not describe one lineup, so decision metrics for that week are omitted.
    """
    user = [fact for fact in facts if fact.get("is_user_team")]
    return all(fact.get("generated_at") == fact.get("roster_generated_at") for fact in user)


def roster_generated_at(stamps: list[str]) -> str | None:
    """Chronologically newest ledger timestamp, for pairing advice to a run."""
    if not stamps:
        return None
    return max(stamps, key=_run_order_key)


def paired_advice(advices: list[dict[str, Any]], generated_at: str) -> dict[str, Any] | None:
    """Advice written by the same lineup run. An older snapshot is not reused."""
    matches = [advice for advice in advices if advice.get("generated_at") == generated_at]
    if not matches:
        return None
    return matches[-1]


def select_scoring_facts(
    facts: list[dict[str, Any]],
    kickoffs: Mapping[tuple[int, str], str] | None = None,
) -> list[dict[str, Any]]:
    """Rows to score for one roster view per league-week.

    The roster is the newest whole ledger run. A player who is absent from
    that run is not scored, even when an older run still has them. When
    ``kickoffs`` maps ``(week, team)`` to a UTC timestamp:

    - the run's row is scored when its ``generated_at`` is strictly before
      that player's kickoff;
    - otherwise the player's row is taken from the newest earlier run that is
      still before kickoff, and only because they are on the newest roster;
    - no pre-kickoff row, or no kickoff for that team-week, drops the player.

    ``kickoffs is None`` means the caller already limited the sample and did
    not supply a schedule. The newest whole run is used with no time cutoff.
    An empty map means kickoff times are unavailable, including the week's
    first kickoff, so nothing is scored.
    """
    normalized = None if kickoffs is None else _normalize_kickoffs(kickoffs)
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        week = fact.get("week")
        if type(week) is not int:
            continue
        key = (fact.get("league_key"), fact.get("season"), week)
        grouped.setdefault(key, []).append(fact)
    chosen: list[dict[str, Any]] = []
    for rows in grouped.values():
        if normalized is None:
            chosen.extend(_whole_run(rows))
        else:
            chosen.extend(_pre_kickoff_rows(rows, normalized))
    chosen.sort(
        key=lambda row: (
            str(row.get("league_key") or ""),
            row.get("season") or 0,
            row.get("week") or 0,
            str(row.get("native_id") or ""),
            str(row.get("generated_at") or ""),
        )
    )
    return chosen


def _decision_facts(
    facts: list[dict[str, Any]], roster_slots: dict[tuple[str, int], dict[str, int]]
) -> tuple[list[dict[str, Any]], list[str]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for fact in facts:
        key = (fact.get("league_key"), fact.get("season"), fact.get("week"))
        grouped.setdefault(key, []).append(fact)
    eligible: list[dict[str, Any]] = []
    notes: list[str] = []
    for (league_key, _season, week), group in sorted(grouped.items(), key=_note_key):
        user = [fact for fact in group if fact.get("is_user_team")]
        if not user:
            continue
        if not uses_one_advice_ledger(group):
            notes.append(f"{league_key} week {week}: {NOTE_MIXED}")
            continue
        slots = roster_slots.get((league_key, week))
        paired = bool(slots) and any(fact.get("recommended_slot") for fact in user)
        if not paired:
            notes.append(f"{league_key} week {week}: {NOTE_UNPAIRED}")
            continue
        eligible.extend(group)
    return eligible, notes


def _note_key(item: tuple[tuple[Any, ...], list[dict[str, Any]]]) -> tuple[Any, ...]:
    league_key, season, week = item[0]
    return (str(league_key or ""), season or 0, week or 0)


def _whole_run(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs = _runs(rows)
    stamp = max(runs, key=_run_order_key)
    return [_with_roster(row, stamp) for row in runs[stamp]]


def _pre_kickoff_rows(
    rows: list[dict[str, Any]], kickoffs: dict[tuple[int, str], datetime]
) -> list[dict[str, Any]]:
    parsed: list[tuple[datetime, str, list[dict[str, Any]]]] = []
    for stamp, run_rows in _runs(rows).items():
        moment = _parse_utc(stamp)
        if moment is None:
            continue
        parsed.append((moment, stamp, run_rows))
    if not parsed:
        return []
    parsed.sort(key=lambda item: item[0])
    roster_moment, roster_stamp, roster_rows = parsed[-1]
    scored: list[dict[str, Any]] = []
    for player in roster_rows:
        kickoff = _player_kickoff(player, kickoffs)
        if kickoff is None:
            continue
        if roster_moment < kickoff:
            scored.append(_with_hours(player, roster_stamp, roster_moment, kickoff))
            continue
        earlier = [
            item
            for item in parsed
            if item[0] < kickoff and _player_row(item[2], player) is not None
        ]
        if not earlier:
            continue
        moment, _stamp, run_rows = earlier[-1]
        matched = _player_row(run_rows, player)
        if matched is None:
            continue
        scored.append(_with_hours(matched, roster_stamp, moment, kickoff))
    return scored


def _runs(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    runs: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        runs.setdefault(str(row.get("generated_at") or ""), []).append(row)
    return runs


def _player_row(rows: list[dict[str, Any]], player: dict[str, Any]) -> dict[str, Any] | None:
    native_id = str(player.get("native_id") or "")
    if not native_id:
        return None
    for row in rows:
        if str(row.get("native_id") or "") == native_id:
            return row
    return None


def _player_kickoff(
    player: dict[str, Any], kickoffs: dict[tuple[int, str], datetime]
) -> datetime | None:
    week = player.get("week")
    if type(week) is not int:
        return None
    raw_team = player.get("nfl_team")
    team = canonical_team(raw_team) if isinstance(raw_team, str) else None
    if team is None:
        return None
    return kickoffs.get((week, team))


def _normalize_kickoffs(
    kickoffs: Mapping[tuple[int, str], str],
) -> dict[tuple[int, str], datetime]:
    normalized: dict[tuple[int, str], datetime] = {}
    for key, stamp in kickoffs.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        week, team = key
        if type(week) is not int or not isinstance(team, str):
            continue
        canonical = canonical_team(team)
        moment = _parse_utc(stamp)
        if canonical is None or moment is None:
            continue
        normalized[(week, canonical)] = moment
    return normalized


def _with_roster(row: dict[str, Any], roster_stamp: str) -> dict[str, Any]:
    return {**row, "roster_generated_at": roster_stamp}


def _with_hours(
    row: dict[str, Any], roster_stamp: str, generated: datetime, kickoff: datetime
) -> dict[str, Any]:
    hours = (kickoff - generated).total_seconds() / 3600
    return {**row, "roster_generated_at": roster_stamp, "hours_before_kickoff": round(hours, 2)}


def _run_order_key(stamp: str) -> tuple[int, datetime | str]:
    moment = _parse_utc(stamp)
    if moment is None:
        return (0, stamp)
    return (1, moment)


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _source_position_accuracy(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    included = _projection_errors(facts, exclude_dnp=False)
    excluded = _projection_errors(facts, exclude_dnp=True)
    rows = []
    for source, position in sorted(included):
        errors = included[(source, position)]
        without = excluded.get((source, position), [])
        rows.append(
            {
                "source": source,
                "position": position,
                **_error_stats(errors),
                "n_without_dnp": len(without),
                "mae_without_dnp": None if not without else _error_stats(without)["mae"],
                "bias_without_dnp": None if not without else _error_stats(without)["bias"],
            }
        )
    return rows


def _projection_errors(
    facts: list[dict[str, Any]], *, exclude_dnp: bool
) -> dict[tuple[str, str], list[float]]:
    grouped: dict[tuple[str, str], list[float]] = {}
    for fact in facts:
        if fact.get("actual_points") is None:
            continue
        if exclude_dnp and fact.get("dnp_proxy"):
            continue
        actual = float(fact["actual_points"])
        position = fact.get("position") or ""
        for source, projected in (fact.get("source_points") or {}).items():
            if projected is None:
                continue
            grouped.setdefault((source, position), []).append(float(projected) - actual)
    return grouped


def _error_stats(errors: list[float]) -> dict[str, Any]:
    return {
        "n": len(errors),
        "mae": round(sum(abs(error) for error in errors) / len(errors), 2),
        "bias": round(sum(errors) / len(errors), 2),
    }


def _hit_rate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row.get("correct") is not None]
    without = [row for row in scored if not row.get("dnp_proxy")]
    return {**_rate(scored), **_rate(without, suffix="_without_dnp")}


def _rate(rows: list[dict[str, Any]], *, suffix: str = "") -> dict[str, Any]:
    hits = sum(1 for row in rows if row["correct"])
    return {
        f"n{suffix}": len(rows),
        f"hits{suffix}": hits,
        f"hit_rate{suffix}": None if not rows else round(hits / len(rows), 4),
    }


def _regret_trend(
    facts: list[dict[str, Any]], roster_slots: dict[tuple[str, int], dict[str, int]]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for fact in facts:
        key = (fact["league_key"], fact["season"], fact["week"], fact["generated_at"])
        grouped.setdefault(key, []).append(fact)
    rows = []
    for (league_key, season, week, generated_at), group in sorted(grouped.items()):
        slots = roster_slots.get((league_key, week))
        if not slots:
            continue
        if not any(fact.get("actual_points") is not None for fact in group):
            continue
        recommended = _recommended_total(group)
        hindsight = _hindsight_total(group, slots)
        rows.append(
            {
                "league_key": league_key,
                "season": season,
                "week": week,
                "generated_at": generated_at,
                "recommended_total": recommended,
                "hindsight_total": hindsight,
                "regret": round(hindsight - recommended, 2),
            }
        )
    return rows


def _recommended_total(facts: list[dict[str, Any]]) -> float:
    total = 0.0
    for fact in facts:
        if not fact.get("is_user_team") or not is_starter(fact.get("recommended_slot")):
            continue
        if fact.get("actual_points") is None:
            continue
        total += float(fact["actual_points"])
    return round(total, 2)


def hindsight_total(facts: list[dict[str, Any]], roster_slots: dict[str, int]) -> float:
    """Actual points of the greedy optimum on the user roster."""
    return _hindsight_total(facts, roster_slots)


def _hindsight_total(facts: list[dict[str, Any]], roster_slots: dict[str, int]) -> float:
    pool = []
    for fact in facts:
        if not _in_hindsight_pool(fact):
            continue
        status = fact.get("injury_status")
        pool.append(
            {
                "native_id": fact["native_id"],
                "name": fact.get("name") or "",
                "position": fact.get("position"),
                "eligible_positions": list(fact.get("eligible_positions") or []),
                "selected_position": fact.get("actual_selected_position")
                or fact.get("selected_position_at_advice"),
                "points": fact.get("actual_points"),
                "injury": None if not status else {"status": status},
            }
        )
    optimal = optimal_starters(pool, roster_slots)
    return round(sum(float(row["points"]) for row in optimal if row.get("points") is not None), 2)


def _in_hindsight_pool(fact: dict[str, Any]) -> bool:
    if not fact.get("is_user_team") or fact.get("moved"):
        return False
    actual_slot = fact.get("actual_selected_position")
    advice_slot = fact.get("selected_position_at_advice")
    if actual_slot in _UNSTARTABLE_SLOTS:
        return False
    return not (actual_slot is None and advice_slot in _UNSTARTABLE_SLOTS)


def _weeks_without_actuals(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for fact in facts:
        grouped.setdefault((fact["league_key"], fact["week"]), []).append(fact)
    return [
        {"league_key": league_key, "week": week}
        for (league_key, week), group in sorted(grouped.items())
        if all(fact.get("actual_points") is None for fact in group)
    ]


def _index_actuals(
    players: list[dict[str, Any]], league_key: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    exact = {str(player["native_id"]): player for player in players}
    index = dict(exact)
    for player in players:
        raw = str(player["native_id"])
        for key in _join_keys(
            raw,
            league_key=league_key,
            selected_position=player.get("selected_position"),
        ):
            index.setdefault(key, player)
    return index, exact


def _match_actual(
    raw_id: str,
    keys: set[str],
    index: dict[str, dict[str, Any]],
    exact: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if raw_id in exact:
        return exact[raw_id]
    found: list[dict[str, Any]] = []
    for key in keys:
        row = index.get(key)
        if row is not None and row not in found:
            found.append(row)
    if len(found) == 1:
        return found[0]
    return None


def _canonical_id(raw_id: str, keys: set[str]) -> str:
    defenses = sorted(key for key in keys if key.startswith("def:"))
    if defenses:
        return defenses[0]
    return raw_id


def _join_keys(
    native_id: str,
    *,
    league_key: str,
    position: str | None = None,
    projection_key: str | None = None,
    player_key: str | None = None,
    selected_position: str | None = None,
    nfl_team: str | None = None,
) -> set[str]:
    """Ids that name this same player, including a canonical ``def:<team>``."""
    keys: set[str] = set()
    raw = str(native_id or "")
    if raw:
        keys.add(raw)
    provider = league_key.split(":", 1)[0] if ":" in league_key else ""
    for candidate in (raw, projection_key, player_key):
        defense = _defense_text(candidate)
        if defense:
            keys.add(defense)
    if provider == "yahoo":
        yahoo = _yahoo_defense_if_consistent(raw, nfl_team)
        if yahoo:
            keys.add(yahoo)
    looks_defense = (
        is_defense_position(position)
        or selected_position == "DEF"
        or any(
            isinstance(item, str) and item.startswith("def:")
            for item in (raw, projection_key, player_key)
        )
    )
    if looks_defense:
        team_candidate = nfl_team if isinstance(nfl_team, str) else None
        id_candidate = raw if raw and not raw.isdigit() else None
        hit = defense_identity("DEF", team_candidate, id_candidate)
        if hit:
            keys.add(hit[0])
    return keys


def _defense_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("def:"):
        return None
    team = canonical_team(value.removeprefix("def:"))
    if team is None:
        return None
    return f"def:{team}"


def _yahoo_defense_if_consistent(native_id: str, nfl_team: str | None) -> str | None:
    hit = yahoo_defense_identity(native_id)
    if hit is None:
        return None
    named = canonical_team(nfl_team) if isinstance(nfl_team, str) else None
    if named is not None and named != hit[1]:
        return None
    return hit[0]
