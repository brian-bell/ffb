"""Anthropic Messages client: current model ids and transient-error retries."""

import httpx
import pytest

from ffb import claude


class _Response:
    def __init__(self, status: int, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.request = httpx.Request("POST", claude.ANTHROPIC_URL)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=self.request, response=self)

    def json(self):
        return self._payload


def _ok(text="hello"):
    return _Response(200, {"content": [{"type": "text", "text": text}]})


def test_models_are_current_generation():
    assert claude.HAIKU_MODEL == "claude-haiku-4-5"
    assert claude.SONNET_MODEL == "claude-sonnet-5"


def test_complete_claude_retries_rate_limit_and_server_errors(monkeypatch):
    responses = iter([_Response(429, headers={"retry-after": "7"}), _Response(529), _ok()])
    posts, sleeps = [], []
    monkeypatch.setattr(claude.httpx, "post", lambda *a, **k: (posts.append(k), next(responses))[1])
    monkeypatch.setattr(claude, "_sleep", sleeps.append)

    assert claude.complete_claude(model="m", system="s", user="u", api_key="k") == "hello"
    assert len(posts) == 3
    assert sleeps[0] == 7.0
    assert all(post["headers"]["x-api-key"] == "k" for post in posts)


def test_complete_claude_retries_transport_errors(monkeypatch):
    calls = []

    def post(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("reset", request=httpx.Request("POST", claude.ANTHROPIC_URL))
        return _ok()

    monkeypatch.setattr(claude.httpx, "post", post)
    monkeypatch.setattr(claude, "_sleep", lambda _: None)
    assert claude.complete_claude(model="m", system="s", user="u", api_key="k") == "hello"
    assert len(calls) == 2


def test_complete_claude_does_not_retry_client_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(claude.httpx, "post", lambda *a, **k: (calls.append(1), _Response(401))[1])
    monkeypatch.setattr(claude, "_sleep", lambda _: pytest.fail("must not sleep"))
    with pytest.raises(httpx.HTTPStatusError):
        claude.complete_claude(model="m", system="s", user="u", api_key="k")
    assert len(calls) == 1


def test_complete_claude_gives_up_after_max_attempts(monkeypatch):
    calls = []
    monkeypatch.setattr(claude.httpx, "post", lambda *a, **k: (calls.append(1), _Response(503))[1])
    monkeypatch.setattr(claude, "_sleep", lambda _: None)
    with pytest.raises(httpx.HTTPStatusError):
        claude.complete_claude(model="m", system="s", user="u", api_key="k")
    assert len(calls) == claude.MAX_ATTEMPTS
