"""
AlpacaAuth — validates Alpaca API credentials and provides auth headers.

Alpaca uses stateless API key authentication: every request includes two headers.
No session, no keepalive, no reauthentication needed.
"""

import httpx
import structlog

from broker.exceptions import AuthError
from config.settings import Settings

log = structlog.get_logger(__name__)


class AlpacaAuth:
    def __init__(self, settings: Settings) -> None:
        self._key = settings.alpaca_api_key
        self._secret = settings.alpaca_api_secret
        self._trading_url = settings.alpaca_trading_url

    def headers(self) -> dict[str, str]:
        """Auth headers to include on every request."""
        return {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
        }

    def validate_credentials(self) -> None:
        """
        Call GET /v2/account to confirm credentials are valid.
        Raises AuthError if keys are missing, invalid (401), or unreachable.
        Call this once at startup before constructing AlpacaClient.
        """
        if not self._key or not self._secret:
            raise AuthError(
                "ALPACA_API_KEY and ALPACA_API_SECRET must be set in .env"
            )
        try:
            resp = httpx.get(
                f"{self._trading_url}/v2/account",
                headers=self.headers(),
                timeout=10,
            )
        except Exception as exc:
            raise AuthError(f"Alpaca credential check failed: {exc}") from exc

        if resp.status_code == 401:
            raise AuthError(
                "Alpaca returned 401 — check ALPACA_API_KEY and ALPACA_API_SECRET."
            )
        if resp.status_code >= 400:
            raise AuthError(
                f"Alpaca credential check failed with HTTP {resp.status_code}."
            )
        log.info("alpaca.credentials_valid")
