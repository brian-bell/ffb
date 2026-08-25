"""Yahoo OAuth2 token lifecycle (refresh grant only).

The initial authorization-code exchange is the one-time HITL browser step
(ffb-1ct.2); this module handles everything after it: loading the stored
token, refreshing an expired access token, and persisting rotations with
owner-only file permissions. Redaction is absolute — no token or client
secret ever reaches a log line, a repr, or an exception message.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ffb import config

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
USER_AGENT = "ffb/0.1 (personal use)"
# Refresh slightly before actual expiry so an in-flight request can't race it.
_EXPIRY_SKEW_SECONDS = 60.0


class YahooAuthError(Exception):
    """Auth or configuration failure whose message is safe to print and log."""


def _nonempty_str(value: object) -> str:
    """Reject null/structured token fields instead of stringifying them.

    ``str(None)`` would launder a malformed payload into the valid-looking
    token ``"None"``, which then gets persisted and breaks every later sync.
    """
    if isinstance(value, str) and value:
        return value
    raise ValueError("expected a nonempty string")


def _finite_number(value: object) -> float:
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise ValueError("expected a finite number")
    return number


@dataclass(frozen=True)
class YahooCredentials:
    """Registered Yahoo app identity. The secret is excluded from repr/str."""

    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str = config.YAHOO_REDIRECT_URI

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> YahooCredentials:
        missing = [
            name
            for name in ("FFB_YAHOO_CLIENT_ID", "FFB_YAHOO_CLIENT_SECRET")
            if not environ.get(name)
        ]
        if missing:
            raise YahooAuthError("missing Yahoo OAuth configuration: " + ", ".join(missing))
        return cls(
            client_id=environ["FFB_YAHOO_CLIENT_ID"],
            client_secret=environ["FFB_YAHOO_CLIENT_SECRET"],
            redirect_uri=environ.get("FFB_YAHOO_REDIRECT_URI", config.YAHOO_REDIRECT_URI),
        )


@dataclass(frozen=True)
class YahooToken:
    """Stored OAuth token pair. Token values are excluded from repr/str."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float  # unix epoch seconds

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at - _EXPIRY_SKEW_SECONDS


class YahooTokenStore:
    """Owner-only on-disk persistence for the token pair."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> YahooToken | None:
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw)
            return YahooToken(
                access_token=_nonempty_str(data["access_token"]),
                refresh_token=_nonempty_str(data["refresh_token"]),
                expires_at=_finite_number(data["expires_at"]),
            )
        except (ValueError, TypeError, KeyError) as exc:
            # Deliberately does not echo file contents: they may hold a token.
            raise YahooAuthError(
                f"stored Yahoo token at {self.path} is malformed; "
                "delete it and re-run the one-time browser authorization"
            ) from exc

    def save(self, token: YahooToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at,
            }
        )
        # Write-then-rename so an interruption can never truncate the only
        # copy of the refresh token (losing it forces a new browser
        # authorization). The temp file is owner-only from creation.
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        # Exclusive create (after clearing any stale temp from a crashed run)
        # so a pre-placed file or symlink at the temp path fails the open
        # instead of receiving or redirecting the secret.
        tmp_path.unlink(missing_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp_path, flags, 0o600)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)  # os.open's mode is umask-filtered
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


class YahooAuth:
    """Yield a valid access token, refreshing and persisting as needed."""

    def __init__(
        self,
        credentials: YahooCredentials,
        store: YahooTokenStore,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.credentials = credentials
        self.store = store
        self.transport = transport
        self.clock = clock

    def access_token(self) -> str:
        token = self.store.load()
        if token is None:
            raise YahooAuthError(
                f"no stored Yahoo token at {self.store.path}; "
                "complete the one-time browser authorization (ffb-1ct.2) first"
            )
        if token.is_expired(self.clock()):
            log.info("yahoo access token expired or expiring; running refresh grant")
            token = self._refresh(token)
            self.store.save(token)
        return token.access_token

    def _refresh(self, token: YahooToken) -> YahooToken:
        # Yahoo authenticates confidential clients with HTTP Basic on the
        # token endpoint; only the refresh-grant fields belong in the body.
        data = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "redirect_uri": self.credentials.redirect_uri,
        }
        log.info("api request provider=yahoo-auth method=POST url=%s", TOKEN_URL)
        with httpx.Client(transport=self.transport) as client:
            response = client.post(
                TOKEN_URL,
                data=data,
                auth=(self.credentials.client_id, self.credentials.client_secret),
                headers={"User-Agent": USER_AGENT},
                timeout=30.0,
            )
        log.info("api response provider=yahoo-auth status=%s", response.status_code)
        if not response.is_success:
            # The body may echo request parameters; report only the status.
            raise YahooAuthError(f"Yahoo token refresh failed with HTTP {response.status_code}")
        try:
            payload = response.json()
            access = _nonempty_str(payload["access_token"])
            expires_in = _finite_number(payload.get("expires_in", 3600))
            if expires_in <= 0:
                raise ValueError("expires_in must be positive")
            refresh = _nonempty_str(payload.get("refresh_token") or token.refresh_token)
        except (ValueError, TypeError, KeyError) as exc:
            raise YahooAuthError("Yahoo token refresh returned an unexpected payload") from exc
        return YahooToken(
            access_token=access, refresh_token=refresh, expires_at=self.clock() + expires_in
        )
