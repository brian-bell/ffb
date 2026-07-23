"""Board: consensus ⋈ adp + vorp + tiers -> ranked board rows + serializers."""

import json

from ffb.board import board_rows, to_board_json, to_csv, to_markdown

ROSTER = {"RB": 1, "WR": 1, "BN": 1}
NUM_TEAMS = 1
TIER_COUNT = {"RB": 2, "WR": 2, "DEF": 1}
POOLS = {"RB": 10, "WR": 10, "DEF": 10}


def _consensus(key, name, pos, team, points, n=2):
    return {
        "player_key": key,
        "full_name": name,
        "position": pos,
        "team": team,
        "matched": True,
        "source_points": {},
        "consensus": points,
        "n": n,
        "rank": 0,
    }


def _adp(key, name, pos, team, adp, matched=True):
    return {
        "player_key": key,
        "native_id": key.split(":")[-1],
        "full_name": name,
        "position": pos,
        "team": team,
        "bye": 9,
        "adp": adp,
        "adp_high": 1,
        "adp_low": 5,
        "adp_stdev": 0.7,
        "times_drafted": 900,
        "matched": matched,
    }


def _board():
    consensus = [
        _consensus("r1", "Big Back", "RB", "SFO", 100.0),
        _consensus("r2", "Small Back", "RB", "NYJ", 60.0),
        _consensus("w1", "Big Wideout", "WR", "CIN", 90.0),
    ]
    adp = [
        _adp("r1", "Big Back", "RB", "SFO", 1.5),
        _adp("w1", "Big Wideout", "WR", "CIN", 2.0),
        _adp("ffc:def", "Some Defense", "DEF", "SFO", 120.0, matched=False),
    ]
    return board_rows(
        consensus,
        adp,
        roster_slots=ROSTER,
        num_teams=NUM_TEAMS,
        tier_count=TIER_COUNT,
        pools=POOLS,
    )


def test_board_joins_adp_and_leaves_missing_adp_null():
    rows = {r["key"]: r for r in _board()}
    assert rows["r1"]["adp"] == 1.5  # joined
    assert rows["r1"]["bye"] == 9
    assert rows["r2"]["adp"] is None  # projection but no ADP row -> null


def test_board_excludes_unmatched_adp_only_rows():
    rows = {r["key"]: r for r in _board()}
    assert "ffc:def" not in rows


def test_board_sorted_by_vorp_desc_after_excluding_unmatched():
    board = _board()
    # w1 vorp 90 > r1 vorp 40 > r2 vorp 0.
    assert [r["key"] for r in board] == ["w1", "r1", "r2"]
    assert [r["rank"] for r in board] == [1, 2, 3]


def test_board_stamps_pos_rank_and_adp_rank():
    rows = {r["key"]: r for r in _board()}
    assert rows["r1"]["pos_rank"] == 1  # best RB by vorp
    assert rows["r2"]["pos_rank"] == 2
    assert rows["w1"]["pos_rank"] == 1
    # adp_rank is by ADP ascending across all rows that have an ADP.
    assert rows["r1"]["adp_rank"] == 1  # 1.5
    assert rows["w1"]["adp_rank"] == 2  # 2.0
    assert rows["r2"]["adp_rank"] is None  # no ADP row


def test_to_board_json_contract_shape_and_nulls():
    board = _board()
    doc = to_board_json(
        board,
        season=2024,
        num_teams=12,
        roster_slots=ROSTER,
        generated_at="2026-07-21T12:00:00Z",
    )
    assert doc["version"] == 1
    assert doc["season"] == 2024
    assert doc["scoring"] == "league"
    assert doc["num_teams"] == 12
    assert doc["roster_slots"] == ROSTER
    assert doc["generated_at"] == "2026-07-21T12:00:00Z"
    # Round-trips through JSON (self-contained, no non-serializable values).
    parsed = json.loads(json.dumps(doc))
    top = parsed["players"][0]
    assert top["key"] == "w1"
    assert set(top) == {
        "key",
        "name",
        "pos",
        "team",
        "bye",
        "points",
        "n_sources",
        "vorp",
        "tier",
        "rank",
        "pos_rank",
        "adp",
        "adp_rank",
        "adp_high",
        "adp_low",
        "adp_stdev",
        "matched",
    }
    without_adp = next(p for p in parsed["players"] if p["key"] == "r2")
    assert without_adp["bye"] is None
    assert without_adp["adp"] is None
    assert without_adp["adp_rank"] is None


def test_to_csv_has_header_and_a_row_per_player():
    csv_text = to_csv(_board())
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("key,name,pos,team,bye,points")
    assert len(lines) == 1 + 3  # header + 3 matched players
    assert any("Big Wideout" in line for line in lines)


def test_to_markdown_has_position_sections():
    md = to_markdown(_board(), season=2024)
    assert "# 2024 draft cheat sheet" in md
    assert "## RB" in md
    assert "## WR" in md
    assert "Big Back" in md


def test_board_tolerates_null_position_row():
    # ESPN can emit a player whose position doesn't map (position=None). It must
    # not be dropped (house invariant) and must not crash the board/serializers.
    consensus = [
        _consensus("r1", "Big Back", "RB", "SFO", 100.0),
        _consensus("x1", "Position Unknown", None, None, 40.0),
    ]
    board = board_rows(
        consensus,
        [],
        roster_slots=ROSTER,
        num_teams=NUM_TEAMS,
        tier_count=TIER_COUNT,
        pools=POOLS,
    )
    ghost = next(r for r in board if r["key"] == "x1")
    assert ghost["pos"] is None
    # Serializers must not raise on the null position.
    assert "Position Unknown" in to_csv(board)
    assert "Position Unknown" in to_markdown(board, season=2024)


def test_to_csv_escapes_formula_leading_cells():
    # Player fields come from an unauthenticated external source; a value starting
    # with =/+/-/@ must not execute as a spreadsheet formula.
    consensus = [_consensus("=cmd", "=EVIL()", "RB", "@SFO", 100.0)]
    csv_text = to_csv(
        board_rows(
            consensus,
            [],
            roster_slots=ROSTER,
            num_teams=NUM_TEAMS,
            tier_count=TIER_COUNT,
            pools=POOLS,
        )
    )
    assert "'=EVIL()" in csv_text
    assert "'@SFO" in csv_text
    assert "'=cmd" in csv_text


def test_to_csv_escapes_control_prefixed_formula():
    # A leading control char (tab/CR/LF) before a formula still executes in
    # spreadsheets, so it must be escaped too.
    consensus = [_consensus("k", "\t=HYPERLINK('http://evil')", "RB", "SFO", 100.0)]
    csv_text = to_csv(
        board_rows(
            consensus,
            [],
            roster_slots=ROSTER,
            num_teams=NUM_TEAMS,
            tier_count=TIER_COUNT,
            pools=POOLS,
        )
    )
    assert "'\t=HYPERLINK" in csv_text
