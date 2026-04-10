"""
auth.py — Token exchange and credential building for per-user DB access.

Env vars:
    BACKEND_INTERNAL_URL   Internal URL of the backend service (default: http://backend:8000)
"""

import os
import requests


class AuthError(Exception):
    """Raised when token exchange fails or credentials are invalid."""


def exchange_token(token: str) -> dict:
    """
    Exchange a user token for scoped database credentials.

    POST to {BACKEND_INTERNAL_URL}/api/internal/token-exchange with {"token": token}.

    Returns:
        dict with keys: db_user, db_password, db_host, db_port, db_name

    Raises:
        AuthError: if the request fails or the backend returns an error.
    """
    base_url = os.getenv("BACKEND_INTERNAL_URL", "http://backend:8000")
    url = f"{base_url}/api/internal/token-exchange"
    try:
        resp = requests.post(url, json={"token": token}, timeout=5)
    except requests.RequestException as e:
        raise AuthError(f"Token exchange request failed: {e}") from e

    if not resp.ok:
        raise AuthError(
            f"Token exchange rejected (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    try:
        creds = resp.json()
    except ValueError as e:
        raise AuthError(f"Invalid JSON from token-exchange endpoint: {e}") from e

    required = {"db_user", "db_password", "db_host", "db_port", "db_name"}
    missing = required - set(creds.keys())
    if missing:
        raise AuthError(f"Token exchange response missing fields: {missing}")

    return creds


def build_db_url(creds: dict) -> str:
    """
    Build a PostgreSQL connection string from credential dict.

    Args:
        creds: dict with db_user, db_password, db_host, db_port, db_name

    Returns:
        PostgreSQL connection URL string.
    """
    return (
        f"postgresql://{creds['db_user']}:{creds['db_password']}"
        f"@{creds['db_host']}:{creds['db_port']}/{creds['db_name']}"
    )
