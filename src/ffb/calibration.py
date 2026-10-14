"""Walk-forward sit/start calibration. Pure: facts in, params and projections out.

Fits on decision rows from ``outcomes.fact_table`` (the caller joins ledgers to
actuals). Weeks at or after the target week are ignored. Both leagues' rows are
pooled. Empty or thin history is the identity model: equal source weights, no
bias, no Questionable haircut, and no residual spread, so calibrated means
equal the stored consensus.

Readings the spec leaves open, chosen as the smallest defensible rule:

- Prior strengths not named in §4.1 use the bias prior: ``k_sd = k_q = 100``.
- ``n`` below that parameter's prior strength forces identity for it. At or
  above the threshold the shrinkage formulas still pull toward the prior.
- ``n`` counts the latest ledger per ``(league, season, week, native_id)`` with
  a consensus and an actual, including zero-point DNPs. One ``n`` is shared
  across sources. ``MSE_pooled`` averages squared source errors concatenated.
- MSE is floored at ``1e-6`` before inverting. With exactly two sources,
  weights are clamped to ``[0.25, 0.75]`` after renormalizing over the sources
  the player has. Other source counts are only renormalized.
- Bias is the shrunk mean of ``actual − weighted projection``, clamped to ±2.
  Identity weights use the stored consensus so rounding is unchanged.
- The Questionable multiplier uses that pre-bias weighted projection, skips
  non-positive projections, and is clamped to ``[0.5, 1]``. It is applied as
  ``mu = bias + haircut * weighted`` and does not remove those rows from the
  bias fit.
- Residual sd is OLS of ``|actual − mu|`` on ``sqrt(max(mu, 0))``, with a
  negative slope or intercept set to 0 first, then shrunk toward ``(0, 0)``
  by ``n / (n + k_sd)``. Coefficients are clamped to ``a ∈ [0, 40]`` and
  ``c ∈ [0, 15]``. Identity sd is 0. A missing params file is not identity:
  the mean stays the consensus and the sd is null.
"""

from __future__ import annotations

import json
import math
from hashlib import sha256
from typing import Any

from ffb.config import FANTASY_POSITIONS
from ffb.lineup import can_fill, is_unavailable
from ffb.retro import league_snapshot_segment

PARAMS_KIND = "calibration_params"
K_W = 200
K_B = 100
K_SD = 100
K_Q = 100
BIAS_CLAMP = 2.0
WEIGHT_MIN = 0.25
WEIGHT_MAX = 0.75
HAIRCUT_MIN = 0.5
HAIRCUT_MAX = 1.0
SD_A_MAX = 40.0
SD_C_MAX = 15.0
MSE_FLOOR = 1e-6
# "Under 60% to be right" (§4.2). Exactly 60% is not a close call.
CLOSE_CALL_PROBABILITY = 0.6

PRIORS: dict[str, Any] = {
    "k_w": K_W,
    "k_b": K_B,
    "k_sd": K_SD,
    "k_q": K_Q,
    "bias_clamp": BIAS_CLAMP,
    "weight_clamp_min": WEIGHT_MIN,
    "weight_clamp_max": WEIGHT_MAX,
    "haircut_clamp_min": HAIRCUT_MIN,
    "haircut_clamp_max": HAIRCUT_MAX,
    "sd_a_clamp_max": SD_A_MAX,
    "sd_c_clamp_max": SD_C_MAX,
    "mse_floor": MSE_FLOOR,
}

_PARAMS_KEYS = {
    "schema_version",
    "kind",
    "season",
    "week",
    "league_key",
    "pooled",
    "fit_weeks",
    "n_by_position",
    "priors",
    "parameters",
    "sha256",
}
_POSITION_KEYS = {
    "n",
    "n_q",
    "weight_identity",
    "bias",
    "weights",
    "precisions",
    "precision_pooled",
    "haircut_q",
    "sd_a",
    "sd_c",
}
_IDENTITY_POSITION: dict[str, Any] = {
    "weight_identity": True,
    "bias": 0.0,
    "haircut_q": 1.0,
    "sd_a": 0.0,
    "sd_c": 0.0,
    "precisions": {},
    "precision_pooled": 1.0,
    "weights": {},
}


def calibration_params_key(season: int, week: int, league_key: str | None = None) -> str:
    """Per-league path for one week's fit. The default Yahoo league stays flat."""
    return f"calibration/{league_snapshot_segment(league_key)}{season}_week{week}"


def fit_calibration(
    facts: list[dict[str, Any]],
    *,
    season: int,
    week: int,
    league_key: str,
) -> dict[str, Any]:
    """Fit one week from decision rows with ``week <`` the target. Leagues pool."""
    if type(season) is not int or season <= 0:
        raise ValueError("season must be a positive integer")
    if type(week) is not int or week <= 0:
        raise ValueError("week must be a positive integer")
    if not isinstance(league_key, str) or not league_key:
        raise ValueError("league_key must be a nonempty string")
    rows = _decision_rows(facts, season=season, week=week)
    by_position: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_position.setdefault(str(row["position"]), []).append(row)
    parameters = {
        position: _fit_position(by_position[position]) for position in sorted(by_position)
    }
    body = {
        "schema_version": 1,
        "kind": PARAMS_KIND,
        "season": season,
        "week": week,
        "league_key": league_key,
        "pooled": True,
        "fit_weeks": sorted({int(row["week"]) for row in rows}),
        "n_by_position": {position: len(by_position[position]) for position in sorted(by_position)},
        "priors": dict(PRIORS),
        "parameters": parameters,
    }
    return seal_calibration_params(body)


def seal_calibration_params(body: dict[str, Any]) -> dict[str, Any]:
    """Attach sha256 of the canonical JSON body. ``body`` must not contain it."""
    if "sha256" in body:
        raise ValueError("calibration body must not already include sha256")
    encoded = _canonical(body)
    return {**body, "sha256": sha256(encoded).hexdigest()}


def parse_calibration_params(payload: object) -> dict[str, Any]:
    """Validate a sealed params file, including that its sha256 matches."""
    if not isinstance(payload, dict):
        raise ValueError("calibration params must be an object")
    if set(payload) != _PARAMS_KEYS:
        raise ValueError("calibration params have unknown or missing fields")
    if payload["schema_version"] != 1:
        raise ValueError("calibration params schema_version must be 1")
    if payload["kind"] != PARAMS_KIND:
        raise ValueError("calibration params kind must be calibration_params")
    for key in ("season", "week"):
        if type(payload[key]) is not int or payload[key] <= 0:
            raise ValueError(f"calibration params {key} must be a positive integer")
    if not isinstance(payload["league_key"], str) or not payload["league_key"]:
        raise ValueError("calibration params league_key must be a nonempty string")
    if payload["pooled"] is not True:
        raise ValueError("calibration params pooled must be true")
    if not isinstance(payload["fit_weeks"], list) or not all(
        type(item) is int and item > 0 for item in payload["fit_weeks"]
    ):
        raise ValueError("calibration params fit_weeks must be positive integers")
    if payload["fit_weeks"] != sorted(payload["fit_weeks"]):
        raise ValueError("calibration params fit_weeks must be sorted")
    _validate_counts(payload["n_by_position"], "n_by_position")
    if not isinstance(payload["priors"], dict):
        raise ValueError("calibration params priors must be an object")
    if set(payload["priors"]) != set(PRIORS):
        raise ValueError("calibration params priors have unknown or missing fields")
    for key, value in payload["priors"].items():
        _finite(value, f"priors.{key}")
    if not isinstance(payload["parameters"], dict):
        raise ValueError("calibration params parameters must be an object")
    for position, fitted in payload["parameters"].items():
        if position not in FANTASY_POSITIONS:
            raise ValueError(f"calibration params position {position} is not scored")
        _validate_position(fitted, position)
    digest = sha256(
        _canonical({key: payload[key] for key in payload if key != "sha256"})
    ).hexdigest()
    if payload["sha256"] != digest:
        raise ValueError("calibration params sha256 does not match")
    return payload


def annotate_players(
    players: list[dict[str, Any]], params: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Copy roster or ledger rows and attach calibrated mean and sd.

    ``params is None`` is the raw fallback: the mean is the consensus and the
    sd is null. A fitted identity position stores sd 0 instead.
    """
    return [{**player, **_annotated(player, params)} for player in players]


def calibrated_projection(
    player: dict[str, Any], params: dict[str, Any] | None
) -> tuple[float | None, float | None]:
    """Return ``(calibrated_mean, calibrated_sd)`` for one player."""
    annotated = _annotated(player, params)
    return annotated["calibrated_mean"], annotated["calibrated_sd"]


def win_probability(mu_a: float, sd_a: float, mu_b: float, sd_b: float) -> float | None:
    """P(A outscores B) ≈ Φ((μ_A − μ_B) / sqrt(sd_A² + sd_B²)).

    A zero combined variance is 1, 0, or 1/2 by the sign of the mean gap.
    Non-finite inputs return None.
    """
    means = []
    for value in (mu_a, sd_a, mu_b, sd_b):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value):
            return None
        means.append(float(value))
    gap = means[0] - means[2]
    variance = means[1] * means[1] + means[3] * means[3]
    if variance <= 0:
        if gap > 0:
            return 1.0
        if gap < 0:
            return 0.0
        return 0.5
    z = gap / math.sqrt(variance)
    probability = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return min(1.0, max(0.0, probability))


def probability_close_calls(
    players: list[dict[str, Any]],
    optimal: list[dict[str, Any]],
    *,
    threshold: float = CLOSE_CALL_PROBABILITY,
) -> list[dict[str, Any]]:
    """Bench players the optimal lineup is under ``threshold`` to outscore.

    The 1.5-point rule stays in ``compare_lineup``. This list is the
    probability rule beside it: one row per eligible bench player, using the
    starter they are least certain to lose to. A missing sd skips that pair.
    """
    optimal_ids = {str(row.get("native_id") or "") for row in optimal}
    by_id = {str(player.get("native_id") or ""): player for player in players}
    flagged = []
    for player in players:
        identity = str(player.get("native_id") or "")
        if identity in optimal_ids or player.get("points") is None or is_unavailable(player):
            continue
        best = None
        for starter in optimal:
            if starter.get("points") is None or not can_fill(player, starter.get("slot")):
                continue
            source = by_id.get(str(starter.get("native_id") or ""), starter)
            probability = win_probability(
                starter.get("points"),
                source.get("sd"),
                player.get("points"),
                player.get("sd"),
            )
            if probability is None or probability >= threshold:
                continue
            gap = round(float(starter["points"]) - float(player["points"]), 1)
            candidate = {
                "name": player.get("name") or "",
                "points": player.get("points"),
                "versus": starter.get("name") or "",
                "slot": starter.get("slot"),
                "delta": gap,
                "p_win": round(probability, 4),
            }
            if best is None or candidate["p_win"] < best["p_win"]:
                best = candidate
        if best is not None:
            flagged.append(best)
    flagged.sort(key=lambda row: (row["p_win"], row["name"]))
    return flagged


def apply_advice_points(players: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """Copy roster rows so ``points`` are the values ``compare_lineup`` should use.

    Raw keeps stored consensus. Calibrated swaps in ``calibrated_mean`` when
    present and carries ``calibrated_sd`` as ``sd``. Missing means stay on
    consensus, the same fallback missing sources already use.
    """
    applied = []
    for player in players:
        row = dict(player)
        if model == "calibrated":
            mean = player.get("calibrated_mean")
            if mean is not None:
                row["points"] = mean
            row["sd"] = player.get("calibrated_sd")
        else:
            row["sd"] = None
        applied.append(row)
    return applied


def start_probabilities(
    players: list[dict[str, Any]], optimal: list[dict[str, Any]]
) -> dict[str, float | None]:
    """P(this player belongs in the starting lineup) versus the closest alternative.

    Starters get ``min P(starter beats eligible bench)``, or 1 when nobody
    comparable sits. Bench players get ``max P(bench beats an eligible starter)``,
    or 0 when they cannot fill a starter slot. A missing sd is null.
    """
    optimal_ids = {str(row.get("native_id") or "") for row in optimal}
    by_id = {str(player.get("native_id") or ""): player for player in players}
    starters = list(optimal)
    out: dict[str, float | None] = {}
    for player in players:
        ident = str(player.get("native_id") or "")
        if player.get("points") is None or player.get("sd") is None:
            out[ident] = None
            continue
        if ident in optimal_ids:
            starter = next(
                (row for row in starters if str(row.get("native_id") or "") == ident),
                None,
            )
            slot = None if starter is None else starter.get("slot")
            probs = []
            for bench in players:
                bench_id = str(bench.get("native_id") or "")
                if (
                    bench_id in optimal_ids
                    or bench.get("points") is None
                    or is_unavailable(bench)
                    or (slot is not None and not can_fill(bench, slot))
                ):
                    continue
                probability = win_probability(
                    player.get("points"), player.get("sd"), bench.get("points"), bench.get("sd")
                )
                if probability is not None:
                    probs.append(probability)
            out[ident] = 1.0 if not probs else round(min(probs), 4)
            continue
        if is_unavailable(player):
            out[ident] = None
            continue
        probs = []
        for starter in starters:
            if starter.get("points") is None or not can_fill(player, starter.get("slot")):
                continue
            source = by_id.get(str(starter.get("native_id") or ""), starter)
            probability = win_probability(
                player.get("points"),
                player.get("sd"),
                source.get("points"),
                source.get("sd"),
            )
            if probability is not None:
                probs.append(probability)
        out[ident] = 0.0 if not probs else round(max(probs), 4)
    return out


def decorate_advice_report(
    report: dict[str, Any], players: list[dict[str, Any]], model: str
) -> dict[str, Any]:
    """Attach ``model``, ``sd`` / ``p_start``, and the close-call rule for ``model``.

    Raw keeps the 1.5-point list from ``compare_lineup``. Calibrated replaces it
    with win-probability close calls. Null sd / p_start are omitted so a raw
    publish stays additive versus already-stored v1 cards.
    """
    probs = start_probabilities(players, report["optimal"])
    for player in players:
        player["p_start"] = probs.get(str(player.get("native_id") or ""))
    by_id = {str(player.get("native_id") or ""): player for player in players}

    def enrich(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        ident = str(row.get("native_id") or "")
        source = by_id.get(ident, row)
        extra: dict[str, Any] = {}
        sd = source.get("sd")
        p_start = probs.get(ident)
        if sd is not None:
            extra["sd"] = sd
        if p_start is not None:
            extra["p_start"] = p_start
        return {**row, **extra} if extra else dict(row)

    decorated = {**report, "model": model}
    for key in ("current", "optimal", "start", "sit", "undecidable", "missing_projections"):
        decorated[key] = [enrich(row) for row in report[key]]
    decorated["aligned"] = [
        {**pair, "current": enrich(pair["current"]), "optimal": enrich(pair["optimal"])}
        for pair in report["aligned"]
    ]
    if model == "calibrated":
        decorated["close_calls"] = probability_close_calls(players, report["optimal"])
    return decorated


def _annotated(player: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    consensus = _raw_consensus(player)
    sources = _sources(player)
    if params is None:
        return {"calibrated_mean": consensus, "calibrated_sd": None}
    if consensus is None and not sources:
        return {"calibrated_mean": None, "calibrated_sd": None}
    fitted = _IDENTITY_POSITION
    if params.get("parameters"):
        fitted = params["parameters"].get(player.get("position")) or _IDENTITY_POSITION
    weighted = _weighted_projection(consensus, sources, fitted)
    if weighted is None:
        return {"calibrated_mean": None, "calibrated_sd": None}
    mean = _combine(consensus, sources, weighted, fitted, _is_q(player))
    spread = _clamp(float(fitted["sd_a"]), 0.0, SD_A_MAX)
    scale = _clamp(float(fitted["sd_c"]), 0.0, SD_C_MAX)
    sd = round(spread + scale * math.sqrt(max(mean, 0.0)), 2)
    return {"calibrated_mean": mean, "calibrated_sd": sd}


def _decision_rows(facts: list[dict[str, Any]], *, season: int, week: int) -> list[dict[str, Any]]:
    latest: dict[tuple[Any, ...], tuple[str, dict[str, Any]]] = {}
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("season") != season:
            continue
        fact_week = fact.get("week")
        if type(fact_week) is not int or fact_week < 1 or fact_week >= week:
            continue
        if fact.get("actual_points") is None or fact.get("consensus") is None:
            continue
        position = fact.get("position")
        if position not in FANTASY_POSITIONS:
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


def _fit_position(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    squared_by_source: dict[str, list[float]] = {}
    for row in rows:
        actual = float(row["actual_points"])
        for source, projected in _sources(row).items():
            error = actual - projected
            squared_by_source.setdefault(source, []).append(error * error)
    sources = sorted(squared_by_source)
    flat = [value for source in sources for value in squared_by_source[source]]
    mse_pooled = None if not flat else sum(flat) / len(flat)
    precision_pooled = _precision(mse_pooled)
    weight_identity = n < K_W or len(sources) < 2
    precisions = {}
    for source in sources:
        if weight_identity:
            precisions[source] = precision_pooled
            continue
        mse = sum(squared_by_source[source]) / len(squared_by_source[source])
        precisions[source] = (n * _precision(mse) + K_W * precision_pooled) / (n + K_W)
    weights = _normalize_weights(sources, precisions, precision_pooled)
    fitted = {
        "weight_identity": weight_identity,
        "precisions": precisions,
        "precision_pooled": precision_pooled,
        "weights": weights,
    }
    weighted_values = [
        _weighted_projection(_number(row.get("consensus")), _sources(row), fitted) for row in rows
    ]

    if n < K_B:
        bias = 0.0
    else:
        total = sum(
            float(row["actual_points"]) - weighted
            for row, weighted in zip(rows, weighted_values, strict=True)
            if weighted is not None
        )
        bias = _clamp(total / (n + K_B), -BIAS_CLAMP, BIAS_CLAMP)

    shortfalls = []
    for row, weighted in zip(rows, weighted_values, strict=True):
        if weighted is None or weighted <= 0 or not _is_q(row):
            continue
        shortfalls.append(1.0 - float(row["actual_points"]) / weighted)
    n_q = len(shortfalls)
    if n_q < K_Q:
        haircut = 1.0
    else:
        haircut = _clamp(1.0 - sum(shortfalls) / (n_q + K_Q), HAIRCUT_MIN, HAIRCUT_MAX)

    mus: list[float] = []
    absolute: list[float] = []
    for row, weighted in zip(rows, weighted_values, strict=True):
        if weighted is None:
            continue
        mean = _combine(
            _number(row.get("consensus")),
            _sources(row),
            weighted,
            {**fitted, "bias": bias, "haircut_q": haircut},
            _is_q(row),
        )
        mus.append(mean)
        absolute.append(abs(float(row["actual_points"]) - mean))
    sd_a, sd_c = _fit_sd(mus, absolute, n)
    return {
        "n": n,
        "n_q": n_q,
        "weight_identity": weight_identity,
        "bias": bias,
        "weights": weights,
        "precisions": precisions,
        "precision_pooled": precision_pooled,
        "haircut_q": haircut,
        "sd_a": sd_a,
        "sd_c": sd_c,
    }


def _fit_sd(means: list[float], absolute: list[float], n: int) -> tuple[float, float]:
    if n < K_SD or not means:
        return 0.0, 0.0
    xs = [math.sqrt(max(mean, 0.0)) for mean in means]
    slope, intercept = _ols(xs, absolute)
    scale = n / (n + K_SD)
    return (
        _clamp(scale * max(intercept, 0.0), 0.0, SD_A_MAX),
        _clamp(scale * max(slope, 0.0), 0.0, SD_C_MAX),
    )


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float]:
    count = len(xs)
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    var_x = sum((value - mean_x) ** 2 for value in xs)
    if var_x == 0:
        return 0.0, mean_y
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope = cov / var_x
    return slope, mean_y - slope * mean_x


def _weighted_projection(
    consensus: float | None,
    sources: dict[str, float],
    fitted: dict[str, Any],
) -> float | None:
    if fitted.get("weight_identity"):
        if consensus is not None:
            return consensus
        if not sources:
            return None
        return sum(sources.values()) / len(sources)
    if not sources:
        return consensus
    weights = _player_weights(sources, fitted)
    if _weights_equal(weights) and consensus is not None:
        return consensus
    return sum(weights[source] * value for source, value in sources.items())


def _combine(
    consensus: float | None,
    sources: dict[str, float],
    weighted: float,
    fitted: dict[str, Any],
    questionable: bool,
) -> float:
    bias = _clamp(float(fitted["bias"]), -BIAS_CLAMP, BIAS_CLAMP)
    haircut = 1.0
    if questionable:
        haircut = _clamp(float(fitted["haircut_q"]), HAIRCUT_MIN, HAIRCUT_MAX)
    weights = (
        {source: 1.0 for source in sources}
        if fitted.get("weight_identity")
        else _player_weights(sources, fitted)
    )
    if bias == 0.0 and haircut == 1.0 and _weights_equal(weights) and consensus is not None:
        return consensus
    return round(bias + haircut * weighted, 2)


def _player_weights(sources: dict[str, float], fitted: dict[str, Any]) -> dict[str, float]:
    names = list(sources)
    precisions = fitted.get("precisions") or {}
    pooled = float(fitted.get("precision_pooled") or 1.0)
    return _normalize_weights(names, precisions, pooled)


def _normalize_weights(
    sources: list[str], precisions: dict[str, float], precision_pooled: float
) -> dict[str, float]:
    if not sources:
        return {}
    raw = [float(precisions.get(source, precision_pooled)) for source in sources]
    total = sum(raw)
    if total <= 0:
        weights = [1.0 / len(sources) for _ in sources]
    else:
        weights = [value / total for value in raw]
    if len(sources) == 2:
        weights = _clamp_pair(weights)
    return {source: weight for source, weight in zip(sources, weights, strict=True)}


def _clamp_pair(weights: list[float]) -> list[float]:
    clamped = [_clamp(weight, WEIGHT_MIN, WEIGHT_MAX) for weight in weights]
    total = sum(clamped)
    return [weight / total for weight in clamped]


def _weights_equal(weights: dict[str, float]) -> bool:
    if len(weights) <= 1:
        return True
    values = list(weights.values())
    return max(values) - min(values) <= 1e-9


def _precision(mse: float | None) -> float:
    if mse is None:
        return 1.0
    return 1.0 / max(mse, MSE_FLOOR)


def _is_q(row: dict[str, Any]) -> bool:
    if "injury_status" in row:
        status = row.get("injury_status")
    else:
        injury = row.get("injury")
        status = injury.get("status") if isinstance(injury, dict) else None
    return str(status or "").upper() == "QUESTIONABLE"


def _sources(row: dict[str, Any]) -> dict[str, float]:
    raw = row.get("source_points") or {}
    if not isinstance(raw, dict):
        return {}
    sources = {}
    for key, value in raw.items():
        number = _number(value)
        if number is None:
            continue
        sources[str(key)] = number
    return sources


def _raw_consensus(player: dict[str, Any]) -> float | None:
    if "consensus" in player:
        return _number(player.get("consensus"))
    return _number(player.get("points"))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _finite(value: object, name: str) -> float:
    number = _number(value)
    if number is None:
        raise ValueError(f"{name} must be a finite number")
    return number


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _validate_counts(value: object, name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"calibration params {name} must be an object")
    for key, count in value.items():
        if not isinstance(key, str) or type(count) is not int or count < 0:
            raise ValueError(f"calibration params {name} values must be nonnegative integers")


def _validate_position(fitted: object, position: str) -> None:
    if not isinstance(fitted, dict) or set(fitted) != _POSITION_KEYS:
        raise ValueError(f"calibration params {position} has unknown or missing fields")
    if type(fitted["n"]) is not int or fitted["n"] < 0:
        raise ValueError(f"calibration params {position} n must be a nonnegative integer")
    if type(fitted["n_q"]) is not int or fitted["n_q"] < 0:
        raise ValueError(f"calibration params {position} n_q must be a nonnegative integer")
    if not isinstance(fitted["weight_identity"], bool):
        raise ValueError(f"calibration params {position} weight_identity must be a boolean")
    _finite(fitted["bias"], f"{position}.bias")
    _finite(fitted["precision_pooled"], f"{position}.precision_pooled")
    _finite(fitted["haircut_q"], f"{position}.haircut_q")
    _finite(fitted["sd_a"], f"{position}.sd_a")
    _finite(fitted["sd_c"], f"{position}.sd_c")
    for label in ("weights", "precisions"):
        if not isinstance(fitted[label], dict):
            raise ValueError(f"calibration params {position}.{label} must be an object")
        for source, value in fitted[label].items():
            if not isinstance(source, str) or not source:
                raise ValueError(f"calibration params {position}.{label} keys must be strings")
            _finite(value, f"{position}.{label}.{source}")
