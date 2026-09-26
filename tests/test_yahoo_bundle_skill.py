"""The yahoo-league-bundle skill's builder rejects partial roster captures."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".agents"
    / "skills"
    / "yahoo-league-bundle"
    / "scripts"
    / "build_bundle.py"
)
_spec = importlib.util.spec_from_file_location("yahoo_build_bundle", SCRIPT)
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)

POSITIONS = "QB, WR, RB, TE, W/T, W/R/T, W/R/T, DEF, BN, BN, BN, BN, BN, BN, BN"
# Page order (statTable0 then statTable1), not Settings order.
RENDERED = ["QB", "RB", "WR", "TE", "W/T", "W/R/T", "W/R/T", *["BN"] * 6, "DEF", "BN"]
PRIMARY = {"W/T": "WR", "W/R/T": "RB", "BN": "WR"}


def _capture():
    rosters, slots = {}, {}
    for team in range(1, 11):
        slots[str(team)] = list(RENDERED)
        rosters[str(team)] = [
            f"{slot}|{100000 + team if slot == 'DEF' else team * 100 + i}|KC|"
            f"{PRIMARY.get(slot, slot)}|Player {team}-{i}"
            for i, slot in enumerate(RENDERED)
        ]
    return {
        "league_id": "928421",
        "game_keys": ["470"],
        "league_name": "MCFFL",
        "week": 3,
        "roster_positions": POSITIONS,
        "scoring": [[s, label, "1"] for s, label in [*bb.STAT_MAP, *bb.UNMAPPED]],
        "teams": [
            [str(i), "Turkey Supreme" if i == 9 else f"Team {i}", f"mgr{i}"] for i in range(1, 11)
        ],
        "rosters": rosters,
        "slots": slots,
    }


def _build(capture):
    return bb.build(capture, season=2026, user_team="Turkey Supreme", expect_teams=10)


def _players(bundle):
    return sum(len(r["players"]) for r in bundle["rosters"])


def test_full_capture_builds():
    assert _players(_build(_capture())) == 150


def test_empty_rendered_slots_are_not_players():
    capture = _capture()
    # "(Empty)" TE and bench slots still render their slot labels.
    capture["rosters"]["2"] = [
        row for row in capture["rosters"]["2"] if row.split("|")[0] not in {"TE", "BN"}
    ]
    assert _players(_build(capture)) == 150 - 8


def _truncate(capture, rows):
    capture["slots"]["4"] = capture["slots"]["4"][:rows]
    capture["rosters"]["4"] = capture["rosters"]["4"][:rows]


def _extra_slot(capture):
    capture["slots"]["3"].append("IR")


def _unrendered_player(capture):
    capture["rosters"]["5"][0] = capture["rosters"]["5"][0].replace("QB|", "TE|", 1)


def _no_slots(capture):
    del capture["slots"]


def _all_slots_empty(capture):
    capture["rosters"]["4"] = []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: _truncate(c, 9), r"Team 4: rendered roster slots .*\(missing"),
        (lambda c: _truncate(c, 13), r"Team 4: rendered roster slots .*\(missing \{'DEF': 1"),
        (_extra_slot, r"Team 3: .* extra \{'IR': 1\}"),
        (_unrendered_player, r"Team 5: players in unrendered slots \{'TE': 1\}"),
        (_no_slots, "re-run the current capture.js"),
        (_all_slots_empty, "Team 4: captured 0 players"),
    ],
    ids=[
        "truncated-9-of-15",
        "second-table-missing",
        "extra-slot",
        "unrendered-player",
        "legacy-capture",
        "empty-roster",
    ],
)
def test_partial_or_inconsistent_captures_are_rejected(mutate, message):
    capture = _capture()
    mutate(capture)
    with pytest.raises(bb.CaptureError, match=message):
        _build(capture)
