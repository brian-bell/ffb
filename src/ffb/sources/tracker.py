"""Thin HTTP client for the tracker Worker.

``GET /api/actuals?season=&week=`` returns the ``WeeklyActualsBundle`` that
Grok (or a fixture) posted to the Worker; ``GET /api/league/bundle`` returns
the last accepted ``LeagueBundle``; ``POST /api/inseason/{kind}`` stores one
in-season report envelope. Validation lives beside each contract
(``ffb.actuals``, ``ffb.league``, ``ffb.inseason``); this module only moves
bytes. The bearer key is read from the environment and must never reach logs,
errors, or snapshots.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "ffb/0.1 (personal use)"
URL_ENV = "FFB_TRACKER_URL"
KEY_ENV = "FFB_TRACKER_API_KEY"


class TrackerConfigError(RuntimeError):
    """The tracker Worker is not configured in the environment."""


@dataclass(frozen=True)
class TrackerConfig:
    base_url: str
    api_key: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> TrackerConfig:
        base_url = (environ.get(URL_ENV) or "").strip().rstrip("/")
        api_key = (environ.get(KEY_ENV) or "").strip()
        missing = [name for name, value in ((URL_ENV, base_url), (KEY_ENV, api_key)) if not value]
        if missing:
            raise TrackerConfigError(f"set {' and '.join(missing)} to reach the tracker")
        return cls(base_url=base_url, api_key=api_key)

    def __repr__(self) -> str:  # never echo the bearer key
        return f"TrackerConfig(base_url={self.base_url!r}, api_key=<redacted>)"


class TrackerPublishError(RuntimeError):
    """The Worker refused a publish. Carries its structured error, never the key."""

    def __init__(self, status: int, error: str, message: str):
        super().__init__(f"{status} {error}: {message}")
        self.status = status
        self.error = error
        self.message = message


def _headers(cfg: TrackerConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.api_key}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def fetch_actuals(client: httpx.Client, cfg: TrackerConfig, season: int, week: int) -> Any | None:
    """Return the raw stored bundle, or ``None`` when the Worker has none for that week."""
    url = f"{cfg.base_url}/api/actuals"
    log.info("api request provider=tracker method=GET url=%s season=%s week=%s", url, season, week)
    response = client.get(
        url, params={"season": season, "week": week}, headers=_headers(cfg), timeout=30.0
    )
    log.info("api response provider=tracker status=%s", response.status_code)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def fetch_league_bundle(
    client: httpx.Client, cfg: TrackerConfig, league_key: str | None = None
) -> Any | None:
    """Return the Worker's last accepted LeagueBundle, or ``None`` when none is stored."""
    url = f"{cfg.base_url}/api/league/bundle"
    params = {} if league_key is None else {"league": league_key}
    log.info("api request provider=tracker method=GET url=%s league=%s", url, league_key)
    response = client.get(url, params=params, headers=_headers(cfg), timeout=30.0)
    log.info("api response provider=tracker status=%s", response.status_code)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def publish_inseason(
    client: httpx.Client,
    cfg: TrackerConfig,
    envelope: dict[str, Any],
    league_key: str | None = None,
) -> Any:
    """POST one in-season envelope; return the Worker's summary or raise TrackerPublishError.

    ``league_key`` selects which league's KV slot the Worker writes. Omitting it
    means the default league, which is what the pre-rekey keys held.
    """
    kind = envelope["kind"]
    url = f"{cfg.base_url}/api/inseason/{kind}"
    params = {} if league_key is None else {"league": league_key}
    log.info(
        "api request provider=tracker method=POST url=%s season=%s week=%s league=%s",
        url,
        envelope.get("season"),
        envelope.get("week"),
        league_key,
    )
    response = client.post(url, json=envelope, params=params, headers=_headers(cfg), timeout=60.0)
    log.info("api response provider=tracker status=%s", response.status_code)
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        raise TrackerPublishError(
            response.status_code,
            str(body.get("error") or "http_error"),
            str(body.get("message") or response.reason_phrase or "publish rejected"),
        )
    return response.json()
