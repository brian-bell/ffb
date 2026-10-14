"""Walk-forward backtest of raw consensus against shadow calibration.

Pure. The CLI loads ledgers and actuals and passes fact rows in. Week ``W`` is
fit with ``fit_calibration`` on the full sample, which keeps only weeks ``< W``.

Readings §5 leaves open:

- The default run pools every league in the sample. ``pooled=False`` is the
  per-league fit variant and cannot pass the promotion gate.
- A backtest week is an NFL week ``W >= 3`` with at least one labelled player.
  The gate needs six of those, from at least two league keys.
- Mean regret is the unweighted mean of per-league-week regrets. A league-week
  with no roster slots is left out of regret and still counts as a week.
- Both lineups are re-derived with ``compare_lineup``. Raw points are the
  stored consensus. Calibrated points are this fit, never a ledger's stored
  ``calibrated_mean``.
- Hindsight reuses ``outcomes.hindsight_total``.
- The gate's Brier score is the user decision table. League-wide Brier is
  every team's advice-time starter versus eligible bench teammates.
- Tied actuals are left out of Brier. Reliability bins are ``[0, 0.5)``,
  then tenths through ``[0.9, 1]``.
- Weighted decision accuracy uses ``|realized_gap|``.
- Gate MAE is the model point error by position on scored rows, including
  zero-point non-defenses. It fails only above a 0.3 point worsening.
- Source bias is projected minus actual, with and without the DNP proxy.
- Spearman is the unweighted mean of within team-position rank correlations.
  Groups smaller than 2, or with no rank variance, are skipped.
- Raw probabilities are deterministic (sd 0). Bootstrap resamples NFL weeks
  1000 times from seed 0 and reports the 2.5/97.5 percentile of the gate
  statistics. The interval is not itself a gate. Brier must strictly improve.
"""

from __future__ import annotations

import math
import random
from typing import Any

from ffb.calibration import calibrated_projection, fit_calibration, win_probability
from ffb.config import FANTASY_POSITIONS
from ffb.lineup import BENCH_SLOT, UNAVAILABLE_STATUSES, can_fill, compare_lineup, is_starter
from ffb.outcomes import decision_table, hindsight_total
from ffb.retro import LINEUP_MODELS, RAW_CONSENSUS_WARNING

MIN_BACKTEST_WEEK = 3
MIN_GATE_WEEKS = 6
MIN_GATE_LEAGUES = 2
MAE_WORSE_TOLERANCE = 0.3
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 0

REASON_PER_LEAGUE = "per-league backtest is not the promotion gate"
REASON_FEW_WEEKS = "fewer than 6 backtest weeks"
REASON_FEW_LEAGUES = "not pooled across both leagues"
REASON_NO_REGRET = "no decision regret"
REASON_REGRET = "mean regret is worse"
REASON_NO_BRIER = "no decision-table Brier"
REASON_BRIER = "Brier does not improve"

_BINS = ((0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0))
_DECISION_STAMP = "backtest"


def mae_reason(position: str) -> str:
    return f"{position} MAE worse by more than 0.3"


def resolve_advice_model(
    requested: str | None,
    *,
    params: dict[str, Any] | None,
    gate_passed: bool,
) -> tuple[str, str | None]:
    """Choose raw vs calibrated sit/start advice.

    ``None`` follows the promotion gate. ``calibrated`` is opt-in even when the
    gate has not passed, and falls back to raw when params are missing.
    """
    if requested is not None and requested not in LINEUP_MODELS:
        raise ValueError("model must be raw or calibrated")
    if requested == "raw":
        return "raw", None
    if requested == "calibrated":
        if params is None:
            return "raw", RAW_CONSENSUS_WARNING
        return "calibrated", None
    if params is None or not gate_passed:
        return "raw", None
    return "calibrated", None


def walk_forward_backtest(
    facts: list[dict[str, Any]],
    *,
    season: int,
    roster_slots: dict[tuple[str, int], dict[str, int]] | None = None,
    league_key: str | None = None,
    pooled: bool = True,
) -> dict[str, Any]:
    """Score each week ``W >= 3`` with a fit that cannot see week ``W`` or later."""
    if not pooled and not league_key:
        raise ValueError("per-league backtest needs a league_key")
    slots = roster_slots or {}
    scoped = _scoped_facts(facts, season=season, league_key=league_key, pooled=pooled)
    labelled = _latest(scoped, season=season, minimum_week=MIN_BACKTEST_WEEK)
    weeks = sorted({int(fact["week"]) for fact in labelled})
    scored: list[dict[str, Any]] = []
    week_rows: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for week in weeks:
        params = fit_calibration(
            scoped, season=season, week=week, league_key=league_key or "pooled"
        )
        players = [_annotate(fact, params) for fact in labelled if int(fact["week"]) == week]
        scored.extend(players)
        summary, sample = _score_week(players, slots)
        week_rows.append(
            {
                "week": week,
                "fit_weeks": list(params["fit_weeks"]),
                "n_by_position": dict(params["n_by_position"]),
                "parameters": params["parameters"],
                **summary,
            }
        )
        samples.append(sample)
    metrics = _metrics(scored, week_rows, samples)
    return {
        "season": season,
        "pooled": pooled,
        "league_key": None if pooled else league_key,
        "leagues": sorted({str(fact["league_key"]) for fact in scored}),
        "weeks": week_rows,
        "metrics": metrics,
        "bootstrap": _bootstrap(samples),
        "gate": _gate(metrics, week_rows, pooled=pooled),
    }


def _scoped_facts(
    facts: list[dict[str, Any]], *, season: int, league_key: str | None, pooled: bool
) -> list[dict[str, Any]]:
    scoped = []
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("season") != season:
            continue
        if not pooled and fact.get("league_key") != league_key:
            continue
        scoped.append(fact)
    return scoped


def _latest(
    facts: list[dict[str, Any]], *, season: int, minimum_week: int | None = None
) -> list[dict[str, Any]]:
    latest: dict[tuple[Any, ...], tuple[str, dict[str, Any]]] = {}
    for fact in facts:
        fact_week = fact.get("week")
        if type(fact_week) is not int or fact_week < 1:
            continue
        if minimum_week is not None and fact_week < minimum_week:
            continue
        if fact.get("actual_points") is None or fact.get("consensus") is None:
            continue
        if fact.get("position") not in FANTASY_POSITIONS:
            continue
        native_id = str(fact.get("native_id") or "")
        if not native_id:
            continue
        key = (fact.get("league_key"), season, fact_week, native_id)
        stamp = str(fact.get("generated_at") or "")
        current = latest.get(key)
        if current is None or stamp >= current[0]:
            latest[key] = (stamp, fact)
    return [item[1] for item in latest.values()]


def _annotate(fact: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    mean, sd = calibrated_projection(fact, params)
    return {
        **fact,
        "raw_mean": _number(fact.get("consensus")),
        "cal_mean": mean,
        "cal_sd": 0.0 if sd is None else sd,
    }


def _score_week(
    players: list[dict[str, Any]], roster_slots: dict[tuple[str, int], dict[str, int]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for player in players:
        grouped.setdefault(str(player["league_key"]), []).append(player)
    league_regret = []
    regret_raw: list[float] = []
    regret_cal: list[float] = []
    brier_raw: list[tuple[float, float]] = []
    brier_cal: list[tuple[float, float]] = []
    accuracy_raw: list[dict[str, Any]] = []
    accuracy_cal: list[dict[str, Any]] = []
    for league_key in sorted(grouped):
        group = grouped[league_key]
        week = int(group[0]["week"])
        slots = roster_slots.get((league_key, week))
        regret = _league_regret(group, slots)
        if regret is not None:
            league_regret.append({"league_key": league_key, **regret})
            regret_raw.append(regret["raw"])
            regret_cal.append(regret["calibrated"])
        raw_rows, raw_events = _decision_bundle(group, slots, calibrated=False)
        cal_rows, cal_events = _decision_bundle(group, slots, calibrated=True)
        accuracy_raw.extend(raw_rows)
        accuracy_cal.extend(cal_rows)
        brier_raw.extend(raw_events)
        brier_cal.extend(cal_events)
    summary = {
        "leagues": sorted(grouped),
        "regret_raw": _round(_mean(regret_raw)),
        "regret_calibrated": _round(_mean(regret_cal)),
        "league_regret": league_regret,
    }
    sample = {
        "regret_raw": regret_raw,
        "regret_cal": regret_cal,
        "brier_raw": brier_raw,
        "brier_cal": brier_cal,
        "accuracy_raw": accuracy_raw,
        "accuracy_cal": accuracy_cal,
    }
    return summary, sample


def _league_regret(
    players: list[dict[str, Any]], roster_slots: dict[str, int] | None
) -> dict[str, float] | None:
    if not roster_slots or not any(player.get("is_user_team") for player in players):
        return None
    hindsight = hindsight_total(players, roster_slots)
    raw_total = _actual_total(players, _started_ids(players, roster_slots, "raw_mean"))
    cal_total = _actual_total(players, _started_ids(players, roster_slots, "cal_mean"))
    return {
        "hindsight": hindsight,
        "recommended_raw": raw_total,
        "recommended_calibrated": cal_total,
        "raw": round(hindsight - raw_total, 2),
        "calibrated": round(hindsight - cal_total, 2),
    }


def _started_ids(
    players: list[dict[str, Any]], roster_slots: dict[str, int], mean_key: str
) -> set[str]:
    return set(_optimal_slots(players, roster_slots, mean_key))


def _lineup_players(players: list[dict[str, Any]], mean_key: str) -> list[dict[str, Any]]:
    lineup = []
    for player in players:
        if not player.get("is_user_team"):
            continue
        status = player.get("injury_status")
        lineup.append(
            {
                "native_id": player.get("native_id"),
                "name": player.get("name") or "",
                "position": player.get("position"),
                "eligible_positions": list(player.get("eligible_positions") or []),
                "selected_position": player.get("selected_position_at_advice") or BENCH_SLOT,
                "points": player.get(mean_key),
                "projection_key": player.get("native_id"),
                "n": player.get("n") or 0,
                "matched": True,
                "injury": None if not status else {"status": status},
            }
        )
    return lineup


def _optimal_slots(
    players: list[dict[str, Any]], roster_slots: dict[str, int], mean_key: str
) -> dict[str, str]:
    optimal = compare_lineup(_lineup_players(players, mean_key), roster_slots)["optimal"]
    return {str(row.get("native_id") or ""): str(row.get("slot") or "") for row in optimal}


def _actual_total(players: list[dict[str, Any]], native_ids: set[str]) -> float:
    total = 0.0
    for player in players:
        if str(player.get("native_id") or "") not in native_ids:
            continue
        if player.get("actual_points") is None:
            continue
        total += float(player["actual_points"])
    return round(total, 2)


def _decision_bundle(
    players: list[dict[str, Any]],
    roster_slots: dict[str, int] | None,
    *,
    calibrated: bool,
) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    if not roster_slots:
        return [], []
    mean_key = "cal_mean" if calibrated else "raw_mean"
    optimal_slots = _optimal_slots(players, roster_slots, mean_key)
    by_id = {str(player.get("native_id") or ""): player for player in players}
    copied = []
    for player in players:
        if not player.get("is_user_team"):
            continue
        copied.append(
            {
                **player,
                "recommended_slot": _assigned_slot(player, optimal_slots),
                "consensus": player.get(mean_key),
                "generated_at": _DECISION_STAMP,
            }
        )
    rows = decision_table(copied)
    events = []
    for row in rows:
        starter = by_id.get(row["starter_native_id"])
        bench = by_id.get(row["bench_native_id"])
        if starter is None or bench is None:
            continue
        event = _event(starter, bench, calibrated=calibrated)
        if event is not None:
            events.append(event)
    return rows, events


def _assigned_slot(player: dict[str, Any], optimal_slots: dict[str, str]) -> str:
    native_id = str(player.get("native_id") or "")
    if native_id in optimal_slots:
        return optimal_slots[native_id]
    selected = player.get("selected_position_at_advice")
    if selected in {"IR", "IL"}:
        return str(selected)
    return BENCH_SLOT


def _event(
    starter: dict[str, Any], bench: dict[str, Any], *, calibrated: bool
) -> tuple[float, float] | None:
    if starter.get("actual_points") is None or bench.get("actual_points") is None:
        return None
    gap = float(starter["actual_points"]) - float(bench["actual_points"])
    if gap == 0:
        return None
    if calibrated:
        probability = win_probability(
            starter.get("cal_mean"),
            starter.get("cal_sd") or 0.0,
            bench.get("cal_mean"),
            bench.get("cal_sd") or 0.0,
        )
    else:
        probability = win_probability(starter.get("raw_mean"), 0.0, bench.get("raw_mean"), 0.0)
    if probability is None:
        return None
    return probability, 1.0 if gap > 0 else 0.0


def _metrics(
    players: list[dict[str, Any]],
    weeks: list[dict[str, Any]],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    regret_raw = [row["raw"] for week in weeks for row in week["league_regret"]]
    regret_cal = [row["calibrated"] for week in weeks for row in week["league_regret"]]
    raw_events = [event for sample in samples for event in sample["brier_raw"]]
    cal_events = [event for sample in samples for event in sample["brier_cal"]]
    raw_rows = [row for sample in samples for row in sample["accuracy_raw"]]
    cal_rows = [row for sample in samples for row in sample["accuracy_cal"]]
    return {
        "regret": {"raw": _round(_mean(regret_raw)), "calibrated": _round(_mean(regret_cal))},
        "decision_accuracy": {"raw": _accuracy(raw_rows), "calibrated": _accuracy(cal_rows)},
        "brier": {
            "decisions": {
                "raw": _brier_report(raw_events),
                "calibrated": _brier_report(cal_events),
            },
            "league": {
                "raw": _brier_report(_league_events(players, calibrated=False)),
                "calibrated": _brier_report(_league_events(players, calibrated=True)),
            },
        },
        "source_position": _source_position(players),
        "model_position": _model_position(players),
        "spearman": _spearman(players),
    }


def _accuracy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row.get("correct") is not None]
    hits = sum(1 for row in scored if row["correct"])
    weight = 0.0
    weighted_hits = 0.0
    for row in scored:
        gap = row.get("realized_gap")
        if gap is None:
            continue
        magnitude = abs(float(gap))
        weight += magnitude
        if row["correct"]:
            weighted_hits += magnitude
    return {
        "n": len(scored),
        "hits": hits,
        "hit_rate": None if not scored else _round(hits / len(scored)),
        "weighted_hit_rate": None if weight == 0 else _round(weighted_hits / weight),
    }


def _league_events(players: list[dict[str, Any]], *, calibrated: bool) -> list[tuple[float, float]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for player in players:
        team = str(player.get("team_key") or "")
        key = (str(player["league_key"]), int(player["week"]), team)
        grouped.setdefault(key, []).append(player)
    events = []
    for group in grouped.values():
        starters = [
            player for player in group if is_starter(player.get("selected_position_at_advice"))
        ]
        bench = [
            player
            for player in group
            if player.get("selected_position_at_advice") == BENCH_SLOT and not _unavailable(player)
        ]
        for starter in starters:
            slot = starter.get("selected_position_at_advice")
            for other in bench:
                if other.get("native_id") == starter.get("native_id"):
                    continue
                if not can_fill(other, slot):
                    continue
                event = _event(starter, other, calibrated=calibrated)
                if event is not None:
                    events.append(event)
    return events


def _unavailable(player: dict[str, Any]) -> bool:
    return str(player.get("injury_status") or "").upper() in UNAVAILABLE_STATUSES


def _brier_report(events: list[tuple[float, float]]) -> dict[str, Any]:
    bins = []
    last = _BINS[-1][1]
    for low, high in _BINS:
        if high == last:
            chosen = [event for event in events if low <= event[0] <= high]
        else:
            chosen = [event for event in events if low <= event[0] < high]
        observed = None if not chosen else _round(_mean([event[1] for event in chosen]))
        mean_p = None if not chosen else _round(_mean([event[0] for event in chosen]))
        bins.append(
            {"low": low, "high": high, "n": len(chosen), "mean_p": mean_p, "observed": observed}
        )
    return {
        "n": len(events),
        "brier": None if not events else _round(_mean([(p - y) ** 2 for p, y in events])),
        "bins": bins,
    }


def _source_position(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    included: dict[tuple[str, str], list[float]] = {}
    excluded: dict[tuple[str, str], list[float]] = {}
    for player in players:
        if player.get("actual_points") is None:
            continue
        actual = float(player["actual_points"])
        position = str(player.get("position") or "")
        dnp = _dnp(player)
        for source, projected in (player.get("source_points") or {}).items():
            number = _number(projected)
            if number is None:
                continue
            error = number - actual
            included.setdefault((str(source), position), []).append(error)
            if not dnp:
                excluded.setdefault((str(source), position), []).append(error)
    rows = []
    for source, position in sorted(included):
        errors = included[(source, position)]
        without = excluded.get((source, position), [])
        mae, rmse, bias = _error_stats(errors)
        held = _error_stats(without) if without else (None, None, None)
        rows.append(
            {
                "source": source,
                "position": position,
                "n": len(errors),
                "mae": mae,
                "rmse": rmse,
                "bias": bias,
                "n_without_dnp": len(without),
                "mae_without_dnp": held[0],
                "rmse_without_dnp": held[1],
                "bias_without_dnp": held[2],
            }
        )
    return rows


def _model_position(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw: dict[str, list[float]] = {}
    calibrated: dict[str, list[float]] = {}
    for player in players:
        if player.get("actual_points") is None:
            continue
        if player.get("raw_mean") is None or player.get("cal_mean") is None:
            continue
        position = str(player.get("position") or "")
        if position not in FANTASY_POSITIONS:
            continue
        actual = float(player["actual_points"])
        raw.setdefault(position, []).append(float(player["raw_mean"]) - actual)
        calibrated.setdefault(position, []).append(float(player["cal_mean"]) - actual)
    rows = []
    for position in sorted(raw):
        mae_raw, rmse_raw, bias_raw = _error_stats(raw[position])
        mae_cal, rmse_cal, bias_cal = _error_stats(calibrated[position])
        rows.append(
            {
                "position": position,
                "n": len(raw[position]),
                "mae_raw": mae_raw,
                "rmse_raw": rmse_raw,
                "bias_raw": bias_raw,
                "mae_calibrated": mae_cal,
                "rmse_calibrated": rmse_cal,
                "bias_calibrated": bias_cal,
            }
        )
    return rows


def _error_stats(errors: list[float]) -> tuple[float, float, float]:
    count = len(errors)
    mae = _round(sum(abs(error) for error in errors) / count)
    rmse = _round(math.sqrt(sum(error * error for error in errors) / count))
    bias = _round(sum(errors) / count)
    return mae, rmse, bias


def _dnp(player: dict[str, Any]) -> bool:
    actual = player.get("actual_points")
    return actual is not None and float(actual) == 0.0 and player.get("position") != "DEF"


def _spearman(players: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for player in players:
        key = (
            player.get("league_key"),
            player.get("week"),
            player.get("team_key"),
            player.get("position"),
        )
        grouped.setdefault(key, []).append(player)
    raw_values = []
    cal_values = []
    for group in grouped.values():
        raw = _rho(group, "raw_mean")
        calibrated = _rho(group, "cal_mean")
        if raw is not None:
            raw_values.append(raw)
        if calibrated is not None:
            cal_values.append(calibrated)
    return {
        "raw": _round(_mean(raw_values)),
        "calibrated": _round(_mean(cal_values)),
        "n_groups": len(raw_values),
    }


def _rho(group: list[dict[str, Any]], mean_key: str) -> float | None:
    pairs = [
        (float(player[mean_key]), float(player["actual_points"]))
        for player in group
        if player.get(mean_key) is not None and player.get("actual_points") is not None
    ]
    if len(pairs) < 2:
        return None
    return _pearson(_ranks([pair[0] for pair in pairs]), _ranks([pair[1] for pair in pairs]))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor
        while end + 1 < len(order) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        rank = (cursor + end) / 2.0 + 1.0
        for index in range(cursor, end + 1):
            ranks[order[index]] = rank
        cursor = end + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    count = len(xs)
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    var_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y)


def _bootstrap(samples: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [sample for sample in samples if sample["regret_raw"] or sample["brier_raw"]]
    return {
        "draws": BOOTSTRAP_DRAWS,
        "seed": BOOTSTRAP_SEED,
        "regret_delta": _resample(usable, _regret_delta),
        "brier_delta": _resample(usable, _brier_delta),
    }


def _resample(samples: list[dict[str, Any]], statistic) -> dict[str, float] | None:
    if len(samples) < 2:
        return None
    generator = random.Random(BOOTSTRAP_SEED)
    draws = []
    for _ in range(BOOTSTRAP_DRAWS):
        chosen = [samples[generator.randrange(len(samples))] for _ in samples]
        value = statistic(chosen)
        if value is not None:
            draws.append(value)
    if not draws:
        return None
    ordered = sorted(draws)
    return {
        "mean": _round(_mean(draws)),
        "low": _round(_percentile(ordered, 0.025)),
        "high": _round(_percentile(ordered, 0.975)),
    }


def _regret_delta(samples: list[dict[str, Any]]) -> float | None:
    raw = [value for sample in samples for value in sample["regret_raw"]]
    calibrated = [value for sample in samples for value in sample["regret_cal"]]
    if not raw or not calibrated:
        return None
    return _mean(calibrated) - _mean(raw)


def _brier_delta(samples: list[dict[str, Any]]) -> float | None:
    raw = [event for sample in samples for event in sample["brier_raw"]]
    calibrated = [event for sample in samples for event in sample["brier_cal"]]
    if not raw or not calibrated:
        return None
    raw_score = _mean([(p - y) ** 2 for p, y in raw])
    cal_score = _mean([(p - y) ** 2 for p, y in calibrated])
    return cal_score - raw_score


def _percentile(ordered: list[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    rank = fraction * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _gate(metrics: dict[str, Any], weeks: list[dict[str, Any]], *, pooled: bool) -> dict[str, Any]:
    leagues = {league for week in weeks for league in week["leagues"]}
    reasons = []
    if not pooled:
        reasons.append(REASON_PER_LEAGUE)
    if len(weeks) < MIN_GATE_WEEKS:
        reasons.append(REASON_FEW_WEEKS)
    if len(leagues) < MIN_GATE_LEAGUES:
        reasons.append(REASON_FEW_LEAGUES)
    regret = metrics["regret"]
    if regret["raw"] is None or regret["calibrated"] is None:
        reasons.append(REASON_NO_REGRET)
    elif regret["calibrated"] > regret["raw"] + 1e-9:
        reasons.append(REASON_REGRET)
    brier = metrics["brier"]["decisions"]
    raw_brier = brier["raw"]["brier"]
    cal_brier = brier["calibrated"]["brier"]
    if raw_brier is None or cal_brier is None:
        reasons.append(REASON_NO_BRIER)
    elif cal_brier >= raw_brier - 1e-12:
        reasons.append(REASON_BRIER)
    for row in metrics["model_position"]:
        if row["mae_calibrated"] > row["mae_raw"] + MAE_WORSE_TOLERANCE + 1e-9:
            reasons.append(mae_reason(row["position"]))
    return {
        "passed": not reasons,
        "n_weeks": len(weeks),
        "n_leagues": len(leagues),
        "reasons": reasons,
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _round(value: float | None, places: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, places)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)
