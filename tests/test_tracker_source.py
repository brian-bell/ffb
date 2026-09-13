"""Thin tracker client: env config, bearer header, 404 → None, publish, redaction."""

import json
import logging

import httpx
import pytest

from ffb.sources.tracker import (
    TrackerConfig,
    TrackerConfigError,
    TrackerPublishError,
    fetch_actuals,
    fetch_league_bundle,
    publish_inseason,
)


def _cfg():
    return TrackerConfig.from_env(
        {"FFB_TRACKER_URL": "https://tracker.test/", "FFB_TRACKER_API_KEY": "sekrit"}
    )


def test_config_requires_both_env_vars():
    with pytest.raises(TrackerConfigError) as exc:
        TrackerConfig.from_env({"FFB_TRACKER_URL": "https://tracker.test"})
    assert "FFB_TRACKER_API_KEY" in str(exc.value)
    with pytest.raises(TrackerConfigError) as exc:
        TrackerConfig.from_env({})
    assert "FFB_TRACKER_URL" in str(exc.value)


def test_config_strips_trailing_slash_and_redacts_key():
    cfg = _cfg()
    assert cfg.base_url == "https://tracker.test"
    assert "sekrit" not in repr(cfg)


def test_fetch_sends_bearer_and_returns_json(caplog):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"schema_version": 1})

    with caplog.at_level(logging.INFO), httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert fetch_actuals(c, _cfg(), 2024, 3) == {"schema_version": 1}
    assert seen["url"] == "https://tracker.test/api/actuals?season=2024&week=3"
    assert seen["auth"] == "Bearer sekrit"
    assert "sekrit" not in caplog.text


def test_fetch_returns_none_on_404_and_raises_otherwise():
    def not_found(_request):
        return httpx.Response(404, json={"error": "not_found"})

    def denied(_request):
        return httpx.Response(401, json={"error": "unauthorized"})

    with httpx.Client(transport=httpx.MockTransport(not_found)) as c:
        assert fetch_actuals(c, _cfg(), 2024, 3) is None
    with (
        httpx.Client(transport=httpx.MockTransport(denied)) as c,
        pytest.raises(httpx.HTTPStatusError),
    ):
        fetch_actuals(c, _cfg(), 2024, 3)


def test_fetch_league_bundle_returns_json_or_none():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"schema_version": 1, "source": "fixture"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert fetch_league_bundle(c, _cfg())["source"] == "fixture"
    assert seen == {"url": "https://tracker.test/api/league/bundle", "auth": "Bearer sekrit"}
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(404))) as c:
        assert fetch_league_bundle(c, _cfg()) is None


def test_publish_inseason_posts_envelope_and_returns_summary(caplog):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"kind": "ros", "season": 2026, "week": 2, "generated_at": "x"}
        )

    envelope = {"schema_version": 1, "kind": "ros", "season": 2026, "week": 2, "report": {}}
    with caplog.at_level(logging.INFO), httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert publish_inseason(c, _cfg(), envelope)["kind"] == "ros"
    assert seen["url"] == "https://tracker.test/api/inseason/ros"
    assert seen["auth"] == "Bearer sekrit"
    assert seen["body"] == envelope
    assert "sekrit" not in caplog.text


def test_publish_inseason_raises_structured_error_without_the_key():
    def stale(_request):
        return httpx.Response(
            409, json={"error": "stale_report", "message": "stored generated_at is newer"}
        )

    envelope = {"kind": "lineup", "season": 2026, "week": 2}
    with (
        httpx.Client(transport=httpx.MockTransport(stale)) as c,
        pytest.raises(TrackerPublishError) as exc,
    ):
        publish_inseason(c, _cfg(), envelope)
    assert exc.value.status == 409
    assert exc.value.error == "stale_report"
    assert "newer" in exc.value.message
    assert "sekrit" not in str(exc.value)

    def gateway(_request):
        return httpx.Response(502, text="bad")

    with (
        httpx.Client(transport=httpx.MockTransport(gateway)) as c,
        pytest.raises(TrackerPublishError) as exc,
    ):
        publish_inseason(c, _cfg(), envelope)
    assert exc.value.error == "http_error"
