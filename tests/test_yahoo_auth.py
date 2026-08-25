"""Yahoo OAuth token lifecycle minus the initial code exchange.

Covers refresh grant, expiry handling, on-disk storage and permissions, and
credential redaction — all offline via httpx.MockTransport.
"""

import json
import stat

import httpx
import pytest

from ffb import yahoo_auth
from ffb.yahoo_auth import (
    YahooAuth,
    YahooAuthError,
    YahooCredentials,
    YahooToken,
    YahooTokenStore,
)

CLIENT_ID = "test-client-id"
CLIENT_SECRET = "sekret-client-value"
ACCESS = "sekret-access-token"
REFRESH = "sekret-refresh-token"
NEW_ACCESS = "sekret-new-access"
NEW_REFRESH = "sekret-new-refresh"


def _credentials():
    return YahooCredentials(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri="https://ffb.example.invalid/auth/yahoo/callback",
    )


def _store(tmp_path, token=None):
    store = YahooTokenStore(tmp_path / "state" / "yahoo_token.json")
    if token is not None:
        store.save(token)
    return store


def _refresh_transport(recorder):
    def handler(request):
        recorder.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": NEW_ACCESS,
                "refresh_token": NEW_REFRESH,
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

    return httpx.MockTransport(handler)


def test_token_store_round_trips_and_restricts_file_permissions(tmp_path):
    store = _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=1000.0))
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600
    loaded = store.load()
    assert loaded == YahooToken(ACCESS, REFRESH, expires_at=1000.0)


def test_token_store_returns_none_when_absent(tmp_path):
    assert _store(tmp_path).load() is None


def test_token_store_rejects_malformed_file_without_echoing_contents(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not json " + ACCESS)
    with pytest.raises(YahooAuthError) as exc:
        store.load()
    assert ACCESS not in str(exc.value)


def test_fresh_token_is_used_without_any_network_call(tmp_path):
    calls = []
    auth = YahooAuth(
        _credentials(),
        _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=5000.0)),
        transport=_refresh_transport(calls),
        clock=lambda: 1000.0,
    )
    assert auth.access_token() == ACCESS
    assert calls == []


def test_expired_token_triggers_refresh_grant_and_persists_rotation(tmp_path):
    calls = []
    store = _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=1000.0))
    auth = YahooAuth(
        _credentials(), store, transport=_refresh_transport(calls), clock=lambda: 2000.0
    )
    assert auth.access_token() == NEW_ACCESS
    assert len(calls) == 1
    request = calls[0]
    assert request.url == yahoo_auth.TOKEN_URL
    body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == REFRESH
    # Rotated refresh token persisted for the next run.
    saved = store.load()
    assert saved.access_token == NEW_ACCESS
    assert saved.refresh_token == NEW_REFRESH
    assert saved.expires_at == 2000.0 + 3600


def test_token_expiring_within_skew_is_refreshed_early(tmp_path):
    calls = []
    auth = YahooAuth(
        _credentials(),
        _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=1030.0)),
        transport=_refresh_transport(calls),
        clock=lambda: 1000.0,
    )
    assert auth.access_token() == NEW_ACCESS
    assert len(calls) == 1


def test_missing_token_directs_to_one_time_authorization(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    auth = YahooAuth(_credentials(), _store(tmp_path), transport=transport)
    with pytest.raises(YahooAuthError, match="authoriz"):
        auth.access_token()


def test_refresh_failure_raises_without_leaking_credentials(tmp_path, caplog):
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    auth = YahooAuth(
        _credentials(),
        _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=0.0)),
        transport=httpx.MockTransport(handler),
        clock=lambda: 1000.0,
    )
    with caplog.at_level("DEBUG"), pytest.raises(YahooAuthError) as exc:
        auth.access_token()
    for secret in (ACCESS, REFRESH, CLIENT_SECRET):
        assert secret not in str(exc.value)
        assert secret not in caplog.text


def test_credentials_resolve_from_env_and_report_missing_vars(tmp_path):
    creds = YahooCredentials.from_env(
        {
            "FFB_YAHOO_CLIENT_ID": CLIENT_ID,
            "FFB_YAHOO_CLIENT_SECRET": CLIENT_SECRET,
        }
    )
    assert creds.client_id == CLIENT_ID
    with pytest.raises(YahooAuthError, match="FFB_YAHOO_CLIENT_SECRET"):
        YahooCredentials.from_env({"FFB_YAHOO_CLIENT_ID": CLIENT_ID})


def test_secrets_never_appear_in_reprs():
    token = YahooToken(ACCESS, REFRESH, expires_at=1.0)
    creds = _credentials()
    for text in (repr(token), str(token), repr(creds), str(creds)):
        for secret in (ACCESS, REFRESH, CLIENT_SECRET):
            assert secret not in text


def test_successful_refresh_logs_no_token_material(tmp_path, caplog):
    calls = []
    auth = YahooAuth(
        _credentials(),
        _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=0.0)),
        transport=_refresh_transport(calls),
        clock=lambda: 1000.0,
    )
    with caplog.at_level("DEBUG"):
        auth.access_token()
    for secret in (ACCESS, REFRESH, NEW_ACCESS, NEW_REFRESH, CLIENT_SECRET):
        assert secret not in caplog.text


def test_token_file_contents_are_only_the_expected_fields(tmp_path):
    store = _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=1000.0))
    payload = json.loads(store.path.read_text())
    assert set(payload) == {"access_token", "refresh_token", "expires_at"}


def test_null_access_token_in_refresh_response_is_rejected_not_saved(tmp_path):
    def handler(request):
        return httpx.Response(
            200, json={"access_token": None, "refresh_token": NEW_REFRESH, "expires_in": 3600}
        )

    original = YahooToken(ACCESS, REFRESH, expires_at=0.0)
    store = _store(tmp_path, original)
    auth = YahooAuth(
        _credentials(), store, transport=httpx.MockTransport(handler), clock=lambda: 1000.0
    )
    with pytest.raises(YahooAuthError, match="unexpected payload"):
        auth.access_token()
    assert store.load() == original  # the bad token was never persisted


def test_null_token_fields_in_stored_file_are_rejected(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"access_token": None, "refresh_token": REFRESH, "expires_at": 1000.0})
    )
    with pytest.raises(YahooAuthError, match="malformed"):
        store.load()


def test_save_replaces_atomically_leaving_no_temp_file(tmp_path):
    store = _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=1000.0))
    store.save(YahooToken(NEW_ACCESS, NEW_REFRESH, expires_at=2000.0))
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]
    assert store.load() == YahooToken(NEW_ACCESS, NEW_REFRESH, expires_at=2000.0)


def test_save_recovers_from_a_stale_temp_file(tmp_path):
    store = _store(tmp_path, YahooToken(ACCESS, REFRESH, expires_at=1000.0))
    store.path.with_name(store.path.name + ".tmp").write_text("left by a crash")
    store.save(YahooToken(NEW_ACCESS, NEW_REFRESH, expires_at=2000.0))
    assert store.load() == YahooToken(NEW_ACCESS, NEW_REFRESH, expires_at=2000.0)
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]
