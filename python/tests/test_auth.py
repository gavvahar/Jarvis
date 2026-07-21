"""Tests for auth.py — session signing/verification + OIDC login routes."""

import asyncio, app as jarvis, auth

from unittest.mock import AsyncMock, MagicMock, patch
from helpers import _mock_async_client


class TestAuthSession:
    def test_sign_and_verify_roundtrip(self):
        auth.init_signer("test-secret-key")
        token = auth._sign_session("user-123")
        assert auth._verify_session(token) == "user-123"

    def test_verify_rejects_tampered_token(self):
        auth.init_signer("test-secret-key")
        token = auth._sign_session("user-123")
        assert auth._verify_session(token + "x") is None

    def test_verify_rejects_garbage(self):
        auth.init_signer("test-secret-key")
        assert auth._verify_session("not-a-real-token") is None

    def test_get_current_user_no_oidc_returns_local(self):
        with patch.object(auth, "_oidc_config", None):
            request = MagicMock()
            request.cookies = {}
            assert auth._get_current_user(request) == "local"

    def test_get_current_user_with_oidc_no_cookie(self):
        with patch.object(auth, "_oidc_config", {"issuer": "x"}):
            request = MagicMock()
            request.cookies = {}
            assert auth._get_current_user(request) is None

    def test_get_current_user_with_oidc_valid_cookie(self):
        auth.init_signer("test-secret-key")
        token = auth._sign_session("user-456")
        with patch.object(auth, "_oidc_config", {"issuer": "x"}):
            request = MagicMock()
            request.cookies = {"jarvis_session": token}
            assert auth._get_current_user(request) == "user-456"

    def test_get_user_from_environ_no_oidc_returns_local(self):
        with patch.object(auth, "_oidc_config", None):
            assert auth._get_user_from_environ({}) == "local"

    def test_get_user_from_environ_missing_cookie_header(self):
        with patch.object(auth, "_oidc_config", {"issuer": "x"}):
            assert auth._get_user_from_environ({}) is None

    def test_get_user_from_environ_parses_cookie(self):
        auth.init_signer("test-secret-key")
        token = auth._sign_session("user-789")
        environ = {"HTTP_COOKIE": f"other=1; jarvis_session={token}"}
        with patch.object(auth, "_oidc_config", {"issuer": "x"}):
            assert auth._get_user_from_environ(environ) == "user-789"

    def test_fetch_oidc_config_noop_when_no_discovery_url(self):
        with patch("auth.OIDC_DISCOVERY_URL", ""):
            asyncio.run(auth._fetch_oidc_config())

    def test_fetch_oidc_config_success(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"issuer": "https://auth.example.com"})
        with (
            patch("auth.OIDC_DISCOVERY_URL", "https://auth.example.com/.well-known/openid-configuration"),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=mock_resp)),
        ):
            asyncio.run(auth._fetch_oidc_config())
        assert auth._oidc_config == {"issuer": "https://auth.example.com"}
        auth._oidc_config = None

    def test_fetch_oidc_config_handles_failure(self):
        with (
            patch("auth.OIDC_DISCOVERY_URL", "https://auth.example.com/.well-known/openid-configuration"),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("timeout")))),
        ):
            asyncio.run(auth._fetch_oidc_config())


class TestAuthRoutes:
    def test_login_redirects_to_root_when_no_oidc(self, api_client):
        with patch.object(auth, "_oidc_config", None):
            resp = api_client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_login_redirects_to_oidc_when_configured(self, api_client):
        with patch.object(auth, "_oidc_config", {"authorization_endpoint": "https://auth.example.com/authorize"}):
            resp = api_client.get("/login", follow_redirects=False)
        assert resp.status_code == 307 or resp.status_code == 302
        assert resp.headers["location"].startswith("https://auth.example.com/authorize?")
        assert "oidc_state" in resp.cookies

    def test_auth_callback_missing_code_returns_400(self, api_client):
        resp = api_client.get("/auth/callback")
        assert resp.status_code == 400

    def test_auth_callback_state_mismatch_returns_400(self, api_client):
        resp = api_client.get("/auth/callback?code=abc&state=x", cookies={"oidc_state": "y"})
        assert resp.status_code == 400

    def test_auth_callback_success(self, api_client):
        token_resp = MagicMock(status_code=200)
        token_resp.raise_for_status = MagicMock()
        token_resp.json = MagicMock(return_value={"access_token": "at"})
        userinfo_resp = MagicMock(status_code=200)
        userinfo_resp.raise_for_status = MagicMock()
        userinfo_resp.json = MagicMock(return_value={"sub": "user-42", "email": "a@b.com", "groups": []})
        mock_client = _mock_async_client(post=token_resp, get=userinfo_resp)
        with (
            patch.object(auth, "_oidc_config", {"token_endpoint": "https://auth.example.com/token", "userinfo_endpoint": "https://auth.example.com/userinfo"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(jarvis, "_db_ensure_user", new=AsyncMock()) as mock_ensure,
        ):
            resp = api_client.get("/auth/callback?code=abc&state=x", cookies={"oidc_state": "x"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        mock_ensure.assert_awaited_once_with("user-42", "a@b.com", "user")

    def test_auth_callback_token_exchange_failure_returns_502(self, api_client):
        mock_client = _mock_async_client(post=AsyncMock(side_effect=Exception("network down")))
        with (
            patch.object(auth, "_oidc_config", {"token_endpoint": "https://auth.example.com/token", "userinfo_endpoint": "https://auth.example.com/userinfo"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            resp = api_client.get("/auth/callback?code=abc&state=x", cookies={"oidc_state": "x"})
        assert resp.status_code == 502

    def test_logout_redirects_to_login(self, api_client):
        resp = api_client.get("/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"
