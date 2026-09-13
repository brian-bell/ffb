"""Thin fetch for the tracker Worker's weekly actuals endpoint.

``GET /api/actuals?season=&week=`` returns the ``WeeklyActualsBundle`` that
Grok (or a fixture) posted to the Worker. Validation lives in
``ffb.actuals.parse_actuals``; this module only moves bytes. The bearer key
is read from the environment and must never reach logs or snapshots.
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
            raise TrackerConfigError(
                f"set {' and '.join(missing)} to pull actuals from the tracker"
            )
        return cls(base_url=base_url, api_key=api_key)

    def __repr__(self) -> str:  # never echo the bearer key
        return f"TrackerConfig(base_url={self.base_url!r}, api_key=<redacted>)"


def fetch_actuals(client: httpx.Client, cfg: TrackerConfig, season: int, week: int) -> Any | None:
    """Return the raw stored bundle, or ``None`` when the Worker has none for that week."""
    url = f"{cfg.base_url}/api/actuals"
    log.info("api request provider=tracker method=GET url=%s season=%s week=%s", url, season, week)
    response = client.get(
        url,
        params={"season": season, "week": week},
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=30.0,
    )
    log.info("api response provider=tracker status=%s", response.status_code)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()
