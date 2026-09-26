"""Build a LeagueBundle v2 from a capture.js export and validate it.

Usage (from the repo root):

    uv run python .agents/skills/yahoo-league-bundle/scripts/build_bundle.py \
        CAPTURE.json --season 2026 --out BUNDLE.json [--previous OLD_BUNDLE.json]

The capture is the JSON object capture.js leaves on ``window.__ffbCapture``.
The bundle is checked here and then by ``ffb.league.parse_bundle`` (the same
closed-schema contract the Worker enforces) before it is written. Nothing is
posted; delivery is a separate, explicit step.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ffb.league import parse_bundle

OFFENSE = "Offense"
DEFENSE = "Defense/Special Teams"

# Yahoo settings label -> [(stat_key, provider_stat_id)]. Mirrors the stat map in
# docs/grok-bot-prompt.md; ids 16 and 35 fold several stat keys into one label.
STAT_MAP: dict[tuple[str, str], list[tuple[str, str]]] = {
    (OFFENSE, "Passing Yards"): [("pass_yd", "4")],
    (OFFENSE, "Passing Touchdowns"): [("pass_td", "5")],
    (OFFENSE, "Rushing Yards"): [("rush_yd", "9")],
    (OFFENSE, "Rushing Touchdowns"): [("rush_td", "10")],
    (OFFENSE, "Receptions"): [("rec", "11")],
    (OFFENSE, "Receiving Yards"): [("rec_yd", "12")],
    (OFFENSE, "Receiving Touchdowns"): [("rec_td", "13")],
    (OFFENSE, "2-Point Conversions"): [
        ("pass_2pt", "16.pass_2pt"),
        ("rush_2pt", "16.rush_2pt"),
        ("rec_2pt", "16.rec_2pt"),
    ],
    (OFFENSE, "Offensive Fumble Return TD"): [("fum_rec_td", "57")],
    (DEFENSE, "Sack"): [("sack", "32")],
    (DEFENSE, "Interception"): [("int", "33")],
    (DEFENSE, "Fumble Recovery"): [("fum_rec", "34")],
    (DEFENSE, "Touchdown"): [("pass_int_td", "35.pass_int_td"), ("def_fum_td", "35.def_fum_td")],
    (DEFENSE, "Safety"): [("safe", "36")],
    (DEFENSE, "Block Kick"): [("blk_kick", "37")],
    (DEFENSE, "Kickoff and Punt Return Touchdowns"): [("def_ret_td", "49")],
    (DEFENSE, "Points Allowed 0 points"): [("pts_allow_0", "50")],
    (DEFENSE, "Points Allowed 1-6 points"): [("pts_allow_1_6", "51")],
    (DEFENSE, "Points Allowed 7-13 points"): [("pts_allow_7_13", "52")],
    (DEFENSE, "Points Allowed 14-20 points"): [("pts_allow_14_20", "53")],
    (DEFENSE, "Points Allowed 21-27 points"): [("pts_allow_21_27", "54")],
}

# Real Yahoo categories with no projection line: kept as unmapped rules.
UNMAPPED: dict[tuple[str, str], str] = {
    (DEFENSE, "Extra Point Returned"): "82",
}

SLOTS = {"QB", "WR", "RB", "TE", "W/T", "W/R/T", "DEF", "BN", "IR", "IL"}
RESERVE = {"BN", "IR", "IL"}


class CaptureError(ValueError):
    pass


def parse_points(value: str) -> float | int:
    per = re.fullmatch(r"(\d+(?:\.\d+)?) yards per point", value.strip())
    points = 1 / float(per.group(1)) if per else float(value)
    if not math.isfinite(points):
        raise CaptureError(f"non-finite scoring value {value!r}")
    points = round(points, 6)
    return int(points) if points.is_integer() else points


def scoring_settings(rows: list[list[str]]) -> tuple[list[dict], list[dict]]:
    rules: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for section, label, value in rows:
        key = (section, label)
        if key in STAT_MAP:
            points = parse_points(value)
            for stat_key, stat_id in STAT_MAP[key]:
                rules.append(
                    {
                        "stat_key": stat_key,
                        "points": points,
                        "provider_stat_id": stat_id,
                        "provider_name": label,
                    }
                )
        elif key in UNMAPPED:
            unmapped.append(
                {
                    "points": parse_points(value),
                    "provider_stat_id": UNMAPPED[key],
                    "provider_name": label,
                }
            )
        else:
            raise CaptureError(
                f"unknown scoring row {section!r} / {label!r} = {value!r}: add it to "
                "STAT_MAP or UNMAPPED with its Yahoo stat id (never guess the id)"
            )
    for field in ("stat_key", "provider_stat_id"):
        dupes = [k for k, n in Counter(r[field] for r in rules).items() if n > 1]
        if dupes:
            raise CaptureError(f"duplicate scoring {field}: {dupes}")
    return rules, unmapped


def roster_slots(positions: str) -> list[dict[str, Any]]:
    counts = Counter(p.strip() for p in positions.split(","))
    unknown = set(counts) - SLOTS
    if unknown:
        raise CaptureError(f"unknown roster positions {sorted(unknown)}")
    return [
        {"position": pos, "count": n, "is_starting": pos not in RESERVE}
        for pos, n in counts.items()
    ]


def player(row: str, game_key: str) -> dict[str, Any]:
    slot, player_id, nfl_team, positions, name = row.split("|", 4)
    if slot not in SLOTS:
        raise CaptureError(f"unknown slot {slot!r} for {name}")
    if not player_id.isdigit():
        raise CaptureError(f"non-numeric player id {player_id!r} for {name}")
    eligible = ["DEF" if p == "DST" else p for p in positions.split(",")]
    return {
        "native_id": player_id,
        "native_player_key": f"{game_key}.p.{player_id}",
        "name": name.strip(),
        "nfl_team": nfl_team.upper(),
        "primary_position": eligible[0],
        "eligible_positions": eligible,
        "selected_position": slot,
    }


def build(capture: dict[str, Any], *, season: int, user_team: str, expect_teams: int) -> dict:
    game_keys = capture["game_keys"]
    if len(game_keys) != 1:
        raise CaptureError(f"expected one Yahoo game key on the page, saw {game_keys}")
    game_key, league_id, week = game_keys[0], capture["league_id"], capture["week"]
    if not isinstance(week, int) or week < 1:
        raise CaptureError(f"bad current week {week!r}")
    league_key = f"{game_key}.l.{league_id}"
    teams = [
        {
            "team_id": team_id,
            "team_key": f"{league_key}.t.{team_id}",
            "name": name,
            "managers": [manager] if manager else [],
            "is_user_team": name == user_team,
        }
        for team_id, name, manager in capture["teams"]
    ]
    if len(teams) != expect_teams:
        raise CaptureError(f"expected {expect_teams} teams, captured {len(teams)}")
    if sum(t["is_user_team"] for t in teams) != 1:
        raise CaptureError(f"exactly one team must be named {user_team!r}")
    if set(capture["rosters"]) != {t["team_id"] for t in teams}:
        raise CaptureError("roster team ids do not match the teams page")
    rules, unmapped = scoring_settings(capture["scoring"])
    bundle = {
        "schema_version": 2,
        "source": "yahoo",
        "synced_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "league": {
            "league_id": league_id,
            "league_key": league_key,
            "name": capture["league_name"],
            "season": season,
            "current_week": week,
            "num_teams": len(teams),
        },
        "settings": {
            "roster_slots": roster_slots(capture["roster_positions"]),
            "scoring_rules": rules,
            "unmapped_scoring_rules": unmapped,
            "provider_settings": {"scoring_type": "head"},
        },
        "teams": teams,
        "rosters": [
            {
                "team_key": f"{league_key}.t.{team_id}",
                "week": week,
                "players": [player(row, game_key) for row in capture["rosters"][team_id]],
            }
            for team_id, _, _ in capture["teams"]
        ],
    }
    ids = Counter(p["native_id"] for r in bundle["rosters"] for p in r["players"])
    dupes = [i for i, n in ids.items() if n > 1]
    if dupes:
        raise CaptureError(f"player ids on more than one roster (mid-transaction?): {dupes}")
    text = json.dumps(bundle)
    if "@" in text or "<" in text:
        raise CaptureError("bundle contains '@' or '<'; an email or HTML leaked into the capture")
    parse_bundle(bundle, season=season)
    return bundle


def diff(previous: dict[str, Any], bundle: dict[str, Any]) -> list[str]:
    def rows(b: dict[str, Any]) -> dict[str, dict[str, dict]]:
        return {
            r["team_key"]: {p.get("native_id", p.get("yahoo_player_id")): p for p in r["players"]}
            for r in b["rosters"]
        }

    names = {t["team_key"]: t["name"] for t in bundle["teams"]}
    old, new = rows(previous), rows(bundle)
    lines = []
    for team_key, players in new.items():
        before = old.get(team_key, {})
        for pid, p in players.items():
            if pid not in before:
                lines.append(f"{names[team_key]}: added {p['name']} ({p['selected_position']})")
            elif before[pid]["selected_position"] != p["selected_position"]:
                lines.append(
                    f"{names[team_key]}: {p['name']} "
                    f"{before[pid]['selected_position']} -> {p['selected_position']}"
                )
        lines += [
            f"{names[team_key]}: dropped {p['name']}"
            for pid, p in before.items()
            if pid not in players
        ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture", type=Path)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--user-team", default="Turkey Supreme")
    parser.add_argument("--expect-teams", type=int, default=10)
    parser.add_argument("--previous", type=Path, help="stored bundle to diff rosters against")
    args = parser.parse_args(argv)
    try:
        bundle = build(
            json.loads(args.capture.read_text()),
            season=args.season,
            user_team=args.user_team,
            expect_teams=args.expect_teams,
        )
    except (CaptureError, ValueError, KeyError) as exc:
        print(f"capture rejected: {exc}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(bundle, indent=1) + "\n")
    league = bundle["league"]
    players = sum(len(r["players"]) for r in bundle["rosters"])
    print(
        f"{league['name']} {league['season']} week {league['current_week']}: "
        f"{league['num_teams']} teams, {players} players, synced_at {bundle['synced_at']}"
    )
    if args.previous:
        changes = diff(json.loads(args.previous.read_text()), bundle)
        print("\n".join(changes) if changes else "no roster changes vs previous bundle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
