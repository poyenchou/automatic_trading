"""
AlpacaClient — httpx wrapper around the Alpaca REST API.

Responsibilities:
- Serialize requests and deserialize responses into Pydantic models.
- Raise typed exceptions (BrokerError subclasses) instead of raw httpx errors.
- Route requests to the correct base URL (trading vs market data).
- Stay under rate limits with a 100ms sleep between requests.
"""

import time
from typing import Any

import httpx
import structlog

from broker.auth import AlpacaAuth
from broker.exceptions import AuthError, GatewayError, OrderRejectedError, RateLimitError
from broker.models import AccountInfo, OHLCVBar, ScannerRow
from config.settings import Settings

log = structlog.get_logger(__name__)


class AlpacaClient:
    def __init__(self, settings: Settings, auth: AlpacaAuth) -> None:
        self._settings = settings
        self._auth = auth
        self._trading_http = httpx.Client(
            base_url=settings.alpaca_trading_url,
            headers=auth.headers(),
            timeout=30.0,
        )
        self._data_http = httpx.Client(
            base_url=settings.alpaca_data_url,
            headers=auth.headers(),
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_account(self) -> AccountInfo:
        """GET /v2/account — returns account state."""
        data = self._get_trading("/v2/account")
        return AccountInfo(**data)

    # ------------------------------------------------------------------
    # Screener
    # ------------------------------------------------------------------

    def get_top_movers(self, top: int = 20) -> list[ScannerRow]:
        """
        GET /v1beta1/screener/stocks/most-actives?by=volume&top=N
        Returns the most active stocks by volume.
        """
        data = self._get_data(
            "/v1beta1/screener/stocks/most-actives",
            params={"by": "volume", "top": top},
        )
        return [ScannerRow(**row) for row in data.get("most_actives", [])]

    # ------------------------------------------------------------------
    # Historical bars
    # ------------------------------------------------------------------

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "5Min",
        start: str | None = None,
        end: str | None = None,
        limit: int = 200,
    ) -> list[OHLCVBar]:
        """
        GET /v2/stocks/{symbol}/bars
        Returns OHLCV bars in ascending time order.
        start / end are ISO 8601 strings, e.g. "2024-01-15T09:30:00Z".
        """
        params: dict[str, Any] = {"timeframe": timeframe, "limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        data = self._get_data(f"/v2/stocks/{symbol}/bars", params=params)
        return [OHLCVBar(**bar) for bar in data.get("bars", [])]

    # ------------------------------------------------------------------
    # HTTP primitives
    # ------------------------------------------------------------------

    def _get_trading(self, path: str, params: dict[str, Any] | None = None) -> Any:
        time.sleep(0.1)  # 100ms between requests ≈ 10 req/s max
        log.debug("http.get", client="trading", path=path)
        resp = self._trading_http.get(path, params=params)
        return self._handle_response(resp)

    def _get_data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        time.sleep(0.1)
        log.debug("http.get", client="data", path=path)
        resp = self._data_http.get(path, params=params)
        return self._handle_response(resp)

    def _post_trading(self, path: str, body: dict[str, Any] | None = None) -> Any:
        time.sleep(0.1)
        log.debug("http.post", client="trading", path=path)
        resp = self._trading_http.post(path, json=body or {})
        return self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> Any:
        log.debug("http.response", status=resp.status_code, url=str(resp.url))

        if resp.status_code == 401:
            raise AuthError("Alpaca returned 401 — check API credentials.")

        if resp.status_code == 429:
            raise RateLimitError(
                f"Rate limit exceeded (HTTP 429). Retry-After: "
                f"{resp.headers.get('Retry-After', 'unknown')}"
            )

        if resp.status_code == 400:
            body = self._safe_json(resp)
            msg = body.get("message", resp.text) if isinstance(body, dict) else resp.text
            raise OrderRejectedError(f"Order rejected (400): {msg}")

        if resp.status_code >= 500:
            raise GatewayError(
                f"Alpaca error {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )

        resp.raise_for_status()
        return self._safe_json(resp)

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return resp.text

    def close(self) -> None:
        self._trading_http.close()
        self._data_http.close()
