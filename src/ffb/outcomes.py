"""Join prediction ledgers to weekly actuals and score sit/start decisions.

Pure compute. The CLI walks snapshots and passes parsed ledgers, actuals, and
lineup advice in. Identity is ``native_id``, with Yahoo team-defense ids
(``100014``) and ``def:<team>`` treated as the same defense. Names are never a
join key.

``hours_before_kickoff`` is always ``None``: neither the ledger nor the
actuals bundle carries a kickoff time. ``calibrated_mean`` / ``calibrated_sd``
are copied from the ledger when a v2 run stored them, and stay ``None`` on v1.
"""

from __future__ import annotations

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
) -> dict[str, Any]:
    """Cross-week metrics on the latest ledger of each league-week.

    That run is the decision row. Kickoff times are not stored, so "latest
    before kickoff" is the latest ledger for the week. Source accuracy bias
    matches weekly retro: mean(projected − actual). Each accuracy row and the
    hit rates are reported with the DNP proxy rows included and again without
    them.
    """
    chosen = _latest_facts(facts)
    decisions = decision_table(chosen)
    slots = roster_slots or {}
    return {
        "season": season,
        "leagues": sorted({fact["league_key"] for fact in chosen}),
        "source_position_accuracy": _source_position_accuracy(chosen),
        "regret_trend": _regret_trend(chosen, slots),
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


def _latest_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[Any, ...], str] = {}
    for fact in facts:
        key = (fact["league_key"], fact["season"], fact["week"])
        stamp = fact["generated_at"]
        if key not in latest or stamp > latest[key]:
            latest[key] = stamp
    return [
        fact
        for fact in facts
        if fact["generated_at"] == latest[(fact["league_key"], fact["season"], fact["week"])]
    ]


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
