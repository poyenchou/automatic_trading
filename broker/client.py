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
from broker.models import AccountInfo, OHLCVBar, OrderResponse, PositionResponse, ScannerRow
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

    def get_top_movers(self, top: int = 20) -> tuple[list[ScannerRow], str]:
        """
        GET /v1beta1/screener/stocks/most-actives?by=volume&top=N
        Returns (rows, last_updated) where last_updated is an ISO 8601 string.
        """
        data = self._get_data(
            "/v1beta1/screener/stocks/most-actives",
            params={"by": "volume", "top": top},
        )
        rows = [ScannerRow(**row) for row in data.get("most_actives", [])]
        last_updated = data.get("last_updated", "")
        return rows, last_updated

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
        return [OHLCVBar(**bar) for bar in data.get("bars") or []]

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_market_order(self, symbol: str, qty: int, side: str = "buy") -> OrderResponse:
        """
        POST /v2/orders — place a market order.

        Args:
            symbol: Stock ticker.
            qty:    Number of shares (whole shares only).
            side:   "buy" or "sell".
        """
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        data = self._post_trading("/v2/orders", body=body)
        return OrderResponse(**data)

    def place_limit_order(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        side: str = "sell",
        time_in_force: str = "day",
    ) -> OrderResponse:
        """
        POST /v2/orders — place a limit order (used for take-profit).

        Args:
            symbol:        Stock ticker.
            qty:           Number of shares.
            limit_price:   Limit price.
            side:          "buy" or "sell".
            time_in_force: "day" or "gtc".
        """
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit",
            "limit_price": str(round(limit_price, 2)),
            "time_in_force": time_in_force,
        }
        data = self._post_trading("/v2/orders", body=body)
        return OrderResponse(**data)

    def place_stop_order(
        self,
        symbol: str,
        qty: int,
        stop_price: float,
        side: str = "sell",
        time_in_force: str = "day",
    ) -> OrderResponse:
        """
        POST /v2/orders — place a stop order (used for stop-loss).

        Args:
            symbol:     Stock ticker.
            qty:        Number of shares.
            stop_price: Trigger price.
            side:       "buy" or "sell".
        """
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "stop",
            "stop_price": str(round(stop_price, 2)),
            "time_in_force": time_in_force,
        }
        data = self._post_trading("/v2/orders", body=body)
        return OrderResponse(**data)

    def get_order(self, order_id: str) -> OrderResponse:
        """GET /v2/orders/{order_id} — fetch current order state."""
        data = self._get_trading(f"/v2/orders/{order_id}")
        return OrderResponse(**data)

    def cancel_order(self, order_id: str) -> None:
        """DELETE /v2/orders/{order_id} — cancel an open order."""
        time.sleep(0.1)
        log.debug("http.delete", path=f"/v2/orders/{order_id}")
        resp = self._trading_http.delete(f"/v2/orders/{order_id}")
        if resp.status_code != 204:
            self._handle_response(resp)

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_position(self, symbol: str) -> PositionResponse | None:
        """
        GET /v2/positions/{symbol} — fetch open position for a symbol.
        Returns None if no position exists (404).
        """
        try:
            data = self._get_trading(f"/v2/positions/{symbol}")
            return PositionResponse(**data)
        except Exception as exc:
            if "404" in str(exc) or "position does not exist" in str(exc).lower():
                return None
            raise

    def close_position(self, symbol: str) -> OrderResponse:
        """
        DELETE /v2/positions/{symbol} — close an open position at market.
        """
        time.sleep(0.1)
        log.debug("http.delete", path=f"/v2/positions/{symbol}")
        resp = self._trading_http.delete(f"/v2/positions/{symbol}")
        data = self._handle_response(resp)
        return OrderResponse(**data)

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
