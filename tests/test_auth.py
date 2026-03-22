"""Unit tests for AlpacaAuth (broker/auth.py)."""

import httpx
import pytest
import respx

from broker.auth import AlpacaAuth
from broker.exceptions import AuthError


# ---------------------------------------------------------------------------
# headers
# ---------------------------------------------------------------------------


def test_headers_returns_correct_keys(settings):
    auth = AlpacaAuth(settings)
    h = auth.headers()
    assert h["APCA-API-KEY-ID"] == "TESTKEY123"
    assert h["APCA-API-SECRET-KEY"] == "TESTSECRET456"


# ---------------------------------------------------------------------------
# validate_credentials
# ---------------------------------------------------------------------------


@respx.mock
def test_validate_credentials_succeeds_on_200(settings):
    respx.get(f"{settings.alpaca_trading_url}/v2/account").mock(
        return_value=httpx.Response(200, json={"id": "PA123", "status": "ACTIVE"})
    )
    auth = AlpacaAuth(settings)
    auth.validate_credentials()  # should not raise


@respx.mock
def test_validate_credentials_raises_on_401(settings):
    respx.get(f"{settings.alpaca_trading_url}/v2/account").mock(
        return_value=httpx.Response(401)
    )
    auth = AlpacaAuth(settings)
    with pytest.raises(AuthError, match="401"):
        auth.validate_credentials()


@respx.mock
def test_validate_credentials_raises_on_network_failure(settings):
    respx.get(f"{settings.alpaca_trading_url}/v2/account").mock(
        side_effect=httpx.ConnectError("refused")
    )
    auth = AlpacaAuth(settings)
    with pytest.raises(AuthError, match="credential check failed"):
        auth.validate_credentials()


def test_validate_credentials_raises_when_keys_missing(settings):
    settings.alpaca_api_key = ""
    settings.alpaca_api_secret = ""
    auth = AlpacaAuth(settings)
    with pytest.raises(AuthError, match="must be set"):
        auth.validate_credentials()
