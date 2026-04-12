"""
Unit tests for agent/auth.py

Tests: exchange_token (success, failure, timeout), build_db_url.
"""

import pytest
from unittest.mock import patch, MagicMock
import requests

from auth import exchange_token, build_db_url, AuthError


VALID_CREDS = {
    "db_user": "user_abc123",
    "db_password": "secret_pass",
    "db_host": "postgres",
    "db_port": 5432,
    "db_name": "docqa",
}


class TestExchangeToken:

    def test_exchange_token_success(self):
        """10.1: Valid token, backend returns 200 → returns credentials dict."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = VALID_CREDS

        with patch("requests.post", return_value=mock_resp):
            creds = exchange_token("otk_valid_token")

        assert creds == VALID_CREDS

    def test_exchange_token_invalid_raises_auth_error(self):
        """10.2: Backend returns 401 → raises AuthError."""
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 401
        mock_resp.text = "Token expired"

        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(AuthError):
                exchange_token("otk_invalid")

    def test_exchange_token_timeout_raises(self):
        """10.3: Backend unreachable (timeout) → raises AuthError."""
        with patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")):
            with pytest.raises(AuthError):
                exchange_token("otk_any")

    def test_exchange_token_connection_error_raises(self):
        """10.3 variant: Connection error → raises AuthError."""
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError("refused")):
            with pytest.raises(AuthError):
                exchange_token("otk_any")

    def test_exchange_token_missing_fields_raises(self):
        """Backend returns 200 but missing required fields → raises AuthError."""
        incomplete = {"db_user": "user", "db_password": "pass"}  # missing db_host etc.
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = incomplete

        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(AuthError, match="missing fields"):
                exchange_token("otk_incomplete")


class TestBuildDbUrl:

    def test_build_db_url(self):
        """10.4: Credentials dict → correct PostgreSQL URL."""
        url = build_db_url(VALID_CREDS)
        assert url == "postgresql://user_abc123:secret_pass@postgres:5432/docqa"

    def test_build_db_url_default_port(self):
        """10.5: Credentials with custom port → port used correctly."""
        creds = {**VALID_CREDS, "db_port": 5433}
        url = build_db_url(creds)
        assert ":5433/" in url

    def test_build_db_url_format(self):
        """URL format: postgresql://user:pass@host:port/db."""
        url = build_db_url(VALID_CREDS)
        assert url.startswith("postgresql://")
        assert "secret_pass" in url
        assert "user_abc123" in url
