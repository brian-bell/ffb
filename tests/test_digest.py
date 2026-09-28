"""Read-time news digest: headlines + injuries, never numeric rankings."""

from ffb.digest import (
    apply_flags,
    attach_headlines,
    build_digest,
    headline_owners,
    league_owners,
    name_mentioned,
    parse_haiku_flags,
    parse_sonnet_narrative,
    watch_players,
)
from ffb.lineup import attach_injuries


def _player(**overrides):
    row = {
        "player_key": "12626",
        "full_name": "Derrick Henry",
        "position": "RB",
        "team": "BAL",
        "selected_position": "BN",
        "matched": True,
    }
    row.update(overrides)
    return row


def _headline(**overrides):
    row = {
        "native_id": "49800111",
        "source": "espn",
        "headline": "Derrick Henry questionable for Week 1",
        "summary": "Ravens RB Derrick Henry is listed as questionable.",
        "url": "https://www.espn.com/nfl/story/_/id/49800111/henry-questionable",
        "published_at": "2026-09-11T18:00:00Z",
        "fetched_at": "2026-09-12T12:00:00Z",
        "athletes": [{"native_id": "3043078", "full_name": "Derrick Henry"}],
    }
    row.update(overrides)
    return row


def _mention(**overrides):
    row = {
        "headline_id": "49800111",
        "source": "espn",
        "native_id": "3043078",
        "full_name": "Derrick Henry",
        "player_key": "12626",
        "matched": True,
        "position": "RB",
        "team": "BAL",
    }
    row.update(overrides)
    return row


def test_name_mentioned_requires_all_name_tokens():
    text = "Ravens listed Derrick Henry as limited"
    assert name_mentioned("Derrick Henry", text) is True
    assert name_mentioned("Ja'Marr Chase", text) is False
    assert name_mentioned("Henry", "King Henry scored") is True


def test_name_mentioned_requires_contiguous_ordered_tokens():
    assert name_mentioned("Josh Allen", "Josh Jacobs and Keenan Allen practiced fully") is False
    assert name_mentioned("Allen Robinson", "Robinson, Allen listed as questionable") is False
    assert name_mentioned("Josh Allen", "Bills QB Josh Allen Jr. threw four TDs") is True
    # Hyphens collapse to a solid token by design (Amon-Ra), so no match here.
    assert name_mentioned("Derrick Henry", "derrick-henry limited") is False


def test_attach_headlines_uses_mentions_then_unique_name_hits():
    rss = _headline(
        native_id="US-EN-49800121",
        source="espn_rss",
        headline="Derrick Henry limited at practice",
        summary="Baltimore listed Derrick Henry as limited on Friday.",
        url="https://www.espn.com/nfl/story/_/id/49800121/henry-limited",
        athletes=[],
    )
    other = _headline(
        native_id="49800114",
        headline="Week 1 schedule notes",
        summary="Kickoff times and weather.",
        url="https://www.espn.com/nfl/story/_/id/49800114/schedule",
        athletes=[],
    )
    players = attach_headlines(
        [_player()],
        headlines=[_headline(), rss, other],
        mentions=[_mention()],
    )
    urls = [item["url"] for item in players[0]["headlines"]]
    assert _headline()["url"] in urls
    assert rss["url"] in urls
    assert other["url"] not in urls


def test_watch_players_are_unrostered_matched_mentions():
    watch = watch_players(
        mentions=[
            _mention(),
            _mention(
                headline_id="49800112",
                native_id="4500000",
                full_name="Rookie Wideout",
                player_key="16000",
                position="WR",
                team="FA",
            ),
            _mention(
                headline_id="49800113",
                native_id="8888888",
                full_name="Unknown Specialist",
                player_key="espn:8888888",
                matched=False,
            ),
        ],
        roster_keys={"12626"},
        league_keys={"12626", "13971"},
    )
    assert [row["player_key"] for row in watch] == ["16000"]
    assert watch[0]["full_name"] == "Rookie Wideout"


def test_apply_flags_never_writes_points():
    flagged = apply_flags(
        [_player(points=18.4)],
        [{"player_key": "12626", "flag": "QUESTIONABLE", "note": "Limited Friday"}],
    )
    assert flagged[0]["flag"] == "QUESTIONABLE"
    assert flagged[0]["note"] == "Limited Friday"
    assert flagged[0]["points"] == 18.4
    flagged = apply_flags(
        flagged, [{"player_key": "12626", "flag": "OUT", "note": "x", "points": 99}]
    )
    assert flagged[0]["points"] == 18.4
    assert flagged[0]["flag"] == "OUT"


def test_parse_haiku_flags_is_defensive():
    flags = parse_haiku_flags(
        '[{"player_key": "12626", "flag": "Q", "note": "Limited", "points": 12}]'
    )
    assert flags == [{"player_key": "12626", "flag": "Q", "note": "Limited"}]
    assert parse_haiku_flags("not json") == []
    assert parse_haiku_flags('{"player_key": "12626"}') == []


def test_parse_sonnet_narrative_strips_fences():
    assert (
        parse_sonnet_narrative("```\nStay patient with Henry.\n```") == "Stay patient with Henry."
    )
    assert parse_sonnet_narrative("   ") == ""


def test_build_digest_groups_roster_watch_and_other():
    roster = attach_injuries(
        [_player()],
        [
            {
                "player_key": "12626",
                "matched": True,
                "status": "QUESTIONABLE",
                "fetched_at": "2026-09-12T08:00:00Z",
            }
        ],
    )
    headlines = [
        _headline(),
        _headline(
            native_id="49800112",
            headline="Rookie Wideout drawing waiver buzz",
            summary="Unrostered Rookie Wideout is the most-added name.",
            url="https://www.espn.com/nfl/story/_/id/49800112/rookie-wideout",
            athletes=[{"native_id": "4500000", "full_name": "Rookie Wideout"}],
        ),
        _headline(
            native_id="49800114",
            headline="Week 1 schedule notes",
            summary="Kickoff times.",
            url="https://www.espn.com/nfl/story/_/id/49800114/schedule",
            athletes=[],
        ),
    ]
    report = build_digest(
        roster=roster,
        headlines=headlines,
        mentions=[
            _mention(),
            _mention(
                headline_id="49800112",
                native_id="4500000",
                full_name="Rookie Wideout",
                player_key="16000",
                position="WR",
                team="FA",
            ),
        ],
        league_keys={"12626"},
        week=1,
        team_name="Brian's Team",
    )
    assert report["week"] == 1
    assert report["team_name"] == "Brian's Team"
    assert report["injury_as_of"] == "2026-09-12T08:00:00Z"
    assert report["roster"][0]["injury"]["status"] == "QUESTIONABLE"
    assert report["roster"][0]["headlines"][0]["headline"].startswith("Derrick Henry")
    assert [row["player_key"] for row in report["watch"]] == ["16000"]
    assert report["watch"][0]["headlines"][0]["headline"].startswith("Rookie Wideout")
    assert report["other_headlines"][0]["headline"] == "Week 1 schedule notes"
    assert "points" not in report["roster"][0]
    assert report["narrative"] is None


TEAMS = [
    {"team_key": "t.1", "name": "Turkey Supreme", "is_user_team": True},
    {"team_key": "t.5", "name": "WM Diners", "is_user_team": False},
    {"team_key": "t.7", "name": "Wigwams", "is_user_team": False},
]


def _roster_row(team_key, name, position, player_key=None):
    return {
        "team_key": team_key,
        "full_name": name,
        "primary_position": position,
        "player_key": player_key,
        "matched": player_key is not None,
    }


def _week3_report():
    """The 2026-09-27 week-3 shape: rival players in the user's news."""
    roster = [
        _player(player_key="dowdle", full_name="Rico Dowdle", position="RB", team="CAR"),
        _player(player_key="monangai", full_name="Kyle Monangai", position="RB", team="CHI"),
    ]
    headlines = [
        _headline(
            native_id="1",
            headline="Rico Dowdle remains out",
            summary="Carolina ruled Rico Dowdle out again.",
        ),
        _headline(
            native_id="2",
            headline="Kyle Monangai gets more work",
            summary="Caleb Williams leaned on Kyle Monangai in the red zone.",
        ),
        _headline(
            native_id="US-EN-3",
            source="espn_rss",
            headline="Puka Nacua trending toward Week 4 return",
            summary="The Rams expect Puka Nacua back.",
        ),
        _headline(
            native_id="4",
            headline="Rookie Wideout drawing waiver buzz",
            summary="Most-added name.",
        ),
        _headline(native_id="5", headline="Unknown Specialist signs", summary="Practice squad."),
    ]
    mentions = [
        _mention(headline_id="1", full_name="Rico Dowdle", player_key="dowdle"),
        _mention(headline_id="2", full_name="Caleb Williams", player_key="cw", position="QB"),
        _mention(headline_id="4", full_name="Rookie Wideout", player_key="16000", position="WR"),
        _mention(
            headline_id="5",
            full_name="Unknown Specialist",
            player_key="espn:5",
            matched=False,
            position=None,
        ),
    ]
    league_rows = [
        _roster_row("t.1", "Rico Dowdle", "RB", "dowdle"),
        _roster_row("t.1", "Kyle Monangai", "RB", "monangai"),
        _roster_row("t.5", "Puka Nacua", "WR"),
        _roster_row("t.7", "Caleb Williams", "QB", "cw"),
    ]
    report = build_digest(
        roster=roster,
        headlines=headlines,
        mentions=mentions,
        league_keys={"dowdle", "monangai", "cw"},
        week=3,
        team_name="Turkey Supreme",
    )
    return report, mentions, league_owners(league_rows, TEAMS)


def test_league_owners_labels_mine_and_each_rival_team():
    owners = league_owners(
        [
            _roster_row("t.1", "Rico Dowdle", "RB", "dowdle"),
            _roster_row("t.5", "Puka Nacua", "WR"),
            _roster_row("t.9", "Mystery Man", "TE", "mm"),
        ],
        TEAMS,
    )
    assert owners == [
        {"player_key": "dowdle", "full_name": "Rico Dowdle", "position": "RB", "owner": "mine"},
        {
            "player_key": None,
            "full_name": "Puka Nacua",
            "position": "WR",
            "owner": "rostered by WM Diners",
        },
        {
            "player_key": "mm",
            "full_name": "Mystery Man",
            "position": "TE",
            "owner": "rostered by another team",
        },
    ]


def test_headline_owners_labels_every_named_player():
    report, mentions, owners = _week3_report()
    tagged = headline_owners(report, mentions, owners)
    assert tagged[("espn", "1")] == [
        {"full_name": "Rico Dowdle", "position": "RB", "owner": "mine"}
    ]
    assert tagged[("espn", "2")] == [
        {"full_name": "Caleb Williams", "position": "QB", "owner": "rostered by Wigwams"},
        {"full_name": "Kyle Monangai", "position": "RB", "owner": "mine"},
    ]
    # RSS carries no athlete tags; the rostered name in the text still resolves.
    assert tagged[("espn_rss", "US-EN-3")] == [
        {"full_name": "Puka Nacua", "position": "WR", "owner": "rostered by WM Diners"}
    ]
    assert tagged[("espn", "4")] == [
        {"full_name": "Rookie Wideout", "position": "WR", "owner": "free agent"}
    ]
    assert tagged[("espn", "5")] == [
        {"full_name": "Unknown Specialist", "position": None, "owner": "owner unknown"}
    ]


def test_headline_owners_never_guesses():
    report, mentions, owners = _week3_report()
    # No league rosters: a resolved mention is not provably a free agent.
    assert headline_owners(report, mentions, [])[("espn", "4")] == [
        {"full_name": "Rookie Wideout", "position": "WR", "owner": "owner unknown"}
    ]
    # Two teams roster a same-named player: the text hit stays unattributed.
    shared = owners + league_owners([_roster_row("t.7", "Puka Nacua", "WR")], TEAMS)
    assert headline_owners(report, mentions, shared)[("espn_rss", "US-EN-3")] == [
        {"full_name": "Puka Nacua", "position": "WR", "owner": "owner unknown"}
    ]
