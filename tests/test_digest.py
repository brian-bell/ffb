"""Read-time news digest: headlines + injuries, never numeric rankings."""

from ffb.digest import (
    apply_flags,
    attach_headlines,
    build_digest,
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
    assert parse_sonnet_narrative("```\nStay patient with Henry.\n```") == "Stay patient with Henry."
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
