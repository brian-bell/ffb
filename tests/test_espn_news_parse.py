"""Parsing ESPN news JSON and RSS into normalized headline rows."""

import json
from pathlib import Path

import httpx
import pytest

from ffb.sources.espn_news import (
    fetch_news,
    fetch_rss,
    parse_news,
    parse_rss,
    rss_snapshot_key,
    snapshot_key,
)

FIXTURE = Path(__file__).parent / "fixtures" / "espn_news_sample.json"
RSS_FIXTURE = Path(__file__).parent / "fixtures" / "espn_news_rss_sample.json"


def test_snapshot_keys_are_stable():
    assert snapshot_key() == "espn/news_nfl"
    assert rss_snapshot_key() == "espn/news_nfl_rss"


def test_parses_espn_articles_and_athlete_ids():
    rows = parse_news(json.loads(FIXTURE.read_text()))
    assert [row["native_id"] for row in rows] == [
        "49800111",
        "49800112",
        "49800113",
        "49800114",
    ]
    henry = rows[0]
    assert henry["source"] == "espn"
    assert henry["headline"] == "Derrick Henry questionable for Week 1"
    assert "questionable" in henry["summary"].lower()
    assert henry["url"].endswith("henry-questionable")
    assert henry["published_at"] == "2026-09-11T18:00:00Z"
    assert henry["athletes"] == [{"native_id": "3043078", "full_name": "Derrick Henry"}]


def test_skips_malformed_articles_and_blank_headlines():
    rows = parse_news(
        {
            "articles": [
                {"id": 1, "headline": "Keep"},
                {"id": 2},
                "not-an-object",
                {"id": 3, "headline": ""},
            ]
        }
    )
    assert [row["native_id"] for row in rows] == ["1"]


def test_parse_news_returns_empty_for_wrong_shape():
    assert parse_news([]) == []
    assert parse_news({"articles": None}) == []


def test_parses_rss_items_without_athlete_ids():
    rows = parse_rss(json.loads(RSS_FIXTURE.read_text()))
    assert [row["native_id"] for row in rows] == ["US-EN-49800121", "US-EN-49800122"]
    first = rows[0]
    assert first["source"] == "espn_rss"
    assert first["headline"] == "Derrick Henry limited at practice"
    assert first["athletes"] == []
    assert first["url"].endswith("henry-limited")


def test_parse_rss_returns_empty_for_bad_xml():
    assert parse_rss({"xml": "<not-rss"}) == []
    assert parse_rss({}) == []
    assert parse_rss("raw string") == []


def test_fetch_news_hits_the_web_api(monkeypatch):
    payload = json.loads(FIXTURE.read_text())

    def fake_get(url, *, params, headers, timeout):
        assert "site.web.api.espn.com" in url
        assert params["limit"] == 50
        request = httpx.Request("GET", url, params=params, headers=headers)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    assert fetch_news()["articles"][0]["id"] == 49800111


def test_fetch_rss_wraps_xml(monkeypatch):
    xml = json.loads(RSS_FIXTURE.read_text())["xml"]

    def fake_get(url, *, headers, timeout):
        assert url.endswith("/espn/rss/nfl/news")
        request = httpx.Request("GET", url, headers=headers)
        return httpx.Response(200, text=xml, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    assert "Derrick Henry" in fetch_rss()["xml"]


def test_fetch_news_raises_on_http_error(monkeypatch):
    def fake_get(url, *, params, headers, timeout):
        request = httpx.Request("GET", url)
        return httpx.Response(403, text="denied", request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        fetch_news()
