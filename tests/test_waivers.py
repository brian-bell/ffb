"""Waiver targets: unrostered ROS value against the weakest starter each could replace."""

from ffb.waivers import waiver_report

SLOTS = {"QB": 1, "RB": 1, "WR": 1, "W/R/T": 1, "K": 1, "DEF": 1, "BN": 3}


def _roster(player_key, name, position, team="BAL", selected="BN", matched=True):
    return {
        "native_id": player_key,
        "full_name": name,
        "nfl_team": team,
        "primary_position": position,
        "eligible_positions": [position],
        "selected_position": selected,
        "player_key": player_key,
        "matched": matched,
    }


def _ros(player_key, name, position, points, team="BAL", matched=True, n=2):
    return {
        "player_key": player_key,
        "full_name": name,
        "position": position,
        "team": team,
        "matched": matched,
        "consensus": points,
        "n": n,
    }


def _league():
    roster = [
        _roster("qb1", "Mine QB", "QB", selected="QB"),
        _roster("rb1", "Mine RB1", "RB", selected="RB"),
        _roster("rb2", "Mine RB2", "RB", selected="W/R/T"),
        _roster("wr1", "Mine WR1", "WR", selected="WR"),
        _roster("wr2", "Mine WR Bench", "WR"),
        _roster("k1", "Mine K", "K", selected="K"),
    ]
    consensus = [
        _ros("qb1", "Mine QB", "QB", 300.0),
        _ros("rb1", "Mine RB1", "RB", 200.0),
        _ros("rb2", "Mine RB2", "RB", 90.0),
        _ros("wr1", "Mine WR1", "WR", 180.0),
        _ros("wr2", "Mine WR Bench", "WR", 60.0),
        _ros("k1", "Mine K", "K", 120.0),
        _ros("fa-rb", "Free Back", "RB", 130.0),
        _ros("fa-wr", "Free Receiver", "WR", 85.0),
        _ros("fa-qb", "Free Passer", "QB", 250.0),
        _ros("fa-k", "Free Kicker", "K", 125.0),
        _ros("other-rb", "Taken Back", "RB", 220.0),
        _ros("def:SFO", "49ers", "DEF", 110.0, team="SFO"),
        _ros("fa-te", "Free Tightend", "TE", 70.0),
        _ros("loose", "Unmatched Guy", "WR", 999.0, matched=False),
    ]
    rostered = {"qb1", "rb1", "rb2", "wr1", "wr2", "k1", "other-rb"}
    return roster, consensus, rostered


def _by_name(report):
    return {row["name"]: row for row in report["candidates"]}


def test_ranks_unrostered_players_by_gain_over_the_weakest_starter_they_could_replace():
    roster, consensus, rostered = _league()
    report = waiver_report(consensus, roster=roster, rostered_keys=rostered, roster_slots=SLOTS)
    rows = _by_name(report)
    # RB2 (90) holds the flex; Free Back (130) replaces him for +40.
    assert rows["Free Back"]["gain"] == 40.0
    assert rows["Free Back"]["replaces"] == "Mine RB2"
    assert rows["Free Back"]["replaces_slot"] == "W/R/T"
    # Free Receiver (85) can fill WR (180) or the flex (90): neither is weaker.
    assert "Free Receiver" not in rows
    # A defense slot with no rostered DEF is empty, so any DEF is pure gain.
    assert rows["49ers"]["gain"] == 110.0
    assert rows["49ers"]["replaces"] is None
    assert rows["Free Kicker"]["gain"] == 5.0
    names = [row["name"] for row in report["candidates"]]
    assert names == ["49ers", "Free Back", "Free Kicker"]
    assert [row["rank"] for row in report["candidates"]] == [1, 2, 3]


def test_never_lists_rostered_unmatched_or_worse_players():
    roster, consensus, rostered = _league()
    report = waiver_report(consensus, roster=roster, rostered_keys=rostered, roster_slots=SLOTS)
    names = set(_by_name(report))
    assert "Taken Back" not in names  # on another team
    assert "Unmatched Guy" not in names  # unresolved identity never enters
    assert "Free Passer" not in names  # 250 < 300 starter
    assert "Free Tightend" not in names  # 70 < 90 flex
    assert report["free_agents"] == 6


def test_reports_each_starting_slot_with_its_ros_value_weakest_first():
    roster, consensus, rostered = _league()
    report = waiver_report(consensus, roster=roster, rostered_keys=rostered, roster_slots=SLOTS)
    starters = {row["slot"]: row for row in report["starters"]}
    assert starters["W/R/T"]["name"] == "Mine RB2"
    assert starters["DEF"] == {"slot": "DEF", "name": None, "position": None, "ros": None}
    assert [row["slot"] for row in report["starters"]][0] == "DEF"


def test_caps_candidates_per_position():
    roster, consensus, rostered = _league()
    consensus += [_ros(f"fa-rb{i}", f"Back {i:02d}", "RB", 100.0 + i) for i in range(15)]
    report = waiver_report(
        consensus, roster=roster, rostered_keys=rostered, roster_slots=SLOTS, per_position=4
    )
    backs = [row for row in report["candidates"] if row["position"] == "RB"]
    assert [row["name"] for row in backs] == ["Free Back", "Back 14", "Back 13", "Back 12"]


def test_carries_injury_status_and_flags_unmatched_rostered_players():
    roster, consensus, rostered = _league()
    roster.append(_roster("yahoo:9", "Mystery Man", "WR", matched=False))
    injuries = [{"player_key": "fa-rb", "status": "OUT", "matched": True}]
    report = waiver_report(
        consensus,
        roster=roster,
        rostered_keys=rostered,
        roster_slots=SLOTS,
        injuries=injuries,
        unmatched_rostered=["Mystery Man"],
    )
    assert _by_name(report)["Free Back"]["injury"] == "OUT"
    assert _by_name(report)["Free Kicker"]["injury"] is None
    assert report["unmatched_rostered"] == ["Mystery Man"]
    assert report["trending_available"] is False


def test_an_injured_starter_leaves_the_slot_to_the_next_best_rostered_player():
    roster, consensus, rostered = _league()
    injuries = [{"player_key": "wr1", "status": "IR", "matched": True}]
    report = waiver_report(
        consensus, roster=roster, rostered_keys=rostered, roster_slots=SLOTS, injuries=injuries
    )
    # WR1 on IR: the bench WR (60) starts at WR, so Free Receiver (85) is +25.
    assert _by_name(report)["Free Receiver"]["gain"] == 25.0
    assert _by_name(report)["Free Receiver"]["replaces"] == "Mine WR Bench"


def test_a_multi_position_free_agent_is_measured_at_every_slot_it_can_fill():
    roster, consensus, rostered = _league()
    consensus.append(_ros("fa-qbte", "Gadget Passer", "QB", 95.0))
    plain = waiver_report(consensus, roster=roster, rostered_keys=rostered, roster_slots=SLOTS)
    assert "Gadget Passer" not in _by_name(plain)  # 95 never beats the 300 QB
    report = waiver_report(
        consensus,
        roster=roster,
        rostered_keys=rostered,
        roster_slots=SLOTS,
        eligibility={"fa-qbte": ["QB", "TE", "W/R/T"]},
    )
    row = _by_name(report)["Gadget Passer"]
    assert row["replaces"] == "Mine RB2"
    assert row["replaces_slot"] == "W/R/T"
    assert row["gain"] == 5.0


def test_an_unprojected_rostered_player_leaves_the_slot_baseline_unknown_not_zero():
    roster, consensus, rostered = _league()
    roster.append(_roster("def:BAL", "Ravens", "DEF", selected="DEF"))
    rostered.add("def:BAL")
    report = waiver_report(consensus, roster=roster, rostered_keys=rostered, roster_slots=SLOTS)
    # The Ravens have no ROS row: their value is unknown, so no free DEF is
    # scored against them as if the slot were empty.
    assert "49ers" not in _by_name(report)
    starters = {row["slot"]: row for row in report["starters"]}
    assert starters["DEF"] == {"slot": "DEF", "name": "Ravens", "position": "DEF", "ros": None}
    # An injured unprojected player still leaves the slot genuinely empty.
    injuries = [{"player_key": "def:BAL", "status": "IR", "matched": True}]
    report = waiver_report(
        consensus, roster=roster, rostered_keys=rostered, roster_slots=SLOTS, injuries=injuries
    )
    assert _by_name(report)["49ers"]["gain"] == 110.0
