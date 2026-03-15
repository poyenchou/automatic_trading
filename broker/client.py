"""
IBKRClient — thin httpx wrapper around the IBKR Client Portal Gateway REST API.

Responsibilities:
- Serialize requests and deserialize responses into Pydantic models.
- Enforce the gateway's ~10 req/s rate limit via a token-bucket limiter.
- Retry transient errors (5xx, network timeouts) with exponential back-off.
- Raise typed exceptions (BrokerError subclasses) instead of raw httpx errors.
"""

import threading
import time
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from broker.auth import SessionManager
from broker.exceptions import AuthError, GatewayError, OrderRejectedError, RateLimitError
from broker.models import (
    AuthStatus,
    ContractResult,
    MarketDataSnapshot,
    OHLCVBar,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    PositionState,
    ScannerParams,
    ScannerRow,
)
from config.settings import Settings

log = structlog.get_logger(__name__)

# IBKR Client Portal Gateway rate limit: ~10 requests per second.
_RATE_LIMIT_RPS = 10


def _is_transient(exc: BaseException) -> bool:
    """Retry on 5xx gateway errors and network-level failures."""
    if isinstance(exc, GatewayError):
        return exc.status_code >= 500
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    return False


class _TokenBucketLimiter:
    """Simple token bucket that enforces a maximum request rate."""

    def __init__(self, rate: float) -> None:
        self._rate = rate          # tokens per second
        self._tokens = rate        # start full
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last = now
            if self._tokens < 1:
                sleep_for = (1 - self._tokens) / self._rate
                time.sleep(sleep_for)
                self._tokens = 0
            else:
                self._tokens -= 1


class IBKRClient:
    def __init__(self, settings: Settings, session: SessionManager) -> None:
        self._settings = settings
        self._session = session
        self._limiter = _TokenBucketLimiter(rate=_RATE_LIMIT_RPS)
        self._http = httpx.Client(
            base_url=settings.gateway_url,
            verify=settings.gateway_verify_ssl,
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Auth convenience
    # ------------------------------------------------------------------

    @property
    def session(self) -> SessionManager:
        return self._session

    def get_auth_status(self) -> AuthStatus:
        data = self._get("/iserver/auth/status")
        return AuthStatus(**data)

    def get_accounts(self) -> list[str]:
        data = self._get("/iserver/accounts")
        accounts = data.get("accounts", [])
        return accounts

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_market_data_snapshot(
        self, conids: list[int], fields: list[str] | None = None
    ) -> list[MarketDataSnapshot]:
        """
        Fetch a real-time snapshot for one or more contracts.
        fields: list of IBKR field IDs, e.g. ["31", "84", "86", "82", "7762"]
        """
        if fields is None:
            fields = ["31", "84", "86", "82", "7762"]
        params = {
            "conids": ",".join(str(c) for c in conids),
            "fields": ",".join(fields),
        }
        data = self._get("/iserver/marketdata/snapshot", params=params)
        if isinstance(data, list):
            return [MarketDataSnapshot(**row) for row in data]
        return []

    def get_historical_data(
        self,
        conid: int,
        period: str = "1d",
        bar: str = "5mins",
        outside_rth: bool = False,
    ) -> list[OHLCVBar]:
        """
        Fetch OHLCV bars via /iserver/marketdata/history.
        period: "1d", "1w", "1m"  bar: "1min", "5mins", "1h", "1d"
        """
        params = {
            "conid": conid,
            "period": period,
            "bar": bar,
            "outsideRth": str(outside_rth).lower(),
        }
        data = self._get("/iserver/marketdata/history", params=params)
        raw_bars: list[dict] = data.get("data", [])
        bars: list[OHLCVBar] = []
        for rb in raw_bars:
            # Gateway returns timestamp as epoch milliseconds under key "t"
            rb["timestamp"] = rb.pop("t", 0) / 1000  # convert ms → s
            bars.append(OHLCVBar(**rb))
        return bars

    # ------------------------------------------------------------------
    # Contract search / scanner
    # ------------------------------------------------------------------

    def search_by_symbol(self, symbol: str) -> list[ContractResult]:
        data = self._get("/iserver/secdef/search", params={"symbol": symbol, "name": False})
        if isinstance(data, list):
            return [ContractResult(**row) for row in data]
        return []

    def get_scanner_results(self, params: ScannerParams) -> list[ScannerRow]:
        body = {
            "instrument": params.instrument,
            "location": params.location,
            "scanCode": params.scan_code,
            "secType": params.sec_type,
            "filters": params.filters,
        }
        data = self._post("/iserver/scanner/run", body=body)
        rows: list[dict] = data.get("contracts", [])
        results: list[ScannerRow] = []
        for row in rows:
            # Flatten nested structure the gateway sometimes returns
            if "contract" in row:
                row.update(row.pop("contract"))
            results.append(ScannerRow(**row))
        return results

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_order(self, account_id: str, order: OrderRequest) -> OrderResponse:
        body = {"orders": [order.to_gateway_dict()]}
        data = self._post(f"/iserver/account/{account_id}/orders", body=body)
        # Gateway returns a list; take first element
        if isinstance(data, list):
            return OrderResponse(**data[0])
        return OrderResponse(**data)

    def get_orders(self, account_id: str) -> list[OrderStatus]:
        data = self._get(f"/iserver/account/{account_id}/orders")
        raw: list[dict] = data.get("orders", [])
        return [OrderStatus(**row) for row in raw]

    def cancel_order(self, account_id: str, order_id: str) -> None:
        self._delete(f"/iserver/account/{account_id}/order/{order_id}")

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self, account_id: str) -> list[PositionState]:
        data = self._get(f"/portfolio/{account_id}/positions/0")
        if isinstance(data, list):
            return [PositionState(**row) for row in data]
        return []

    # ------------------------------------------------------------------
    # HTTP primitives
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._limiter.acquire()
        log.debug("http.get", path=path)
        resp = self._http.get(path, params=params)
        return self._handle_response(resp)

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        self._limiter.acquire()
        log.debug("http.post", path=path)
        resp = self._http.post(path, json=body or {})
        return self._handle_response(resp)

    def _delete(self, path: str) -> None:
        self._limiter.acquire()
        log.debug("http.delete", path=path)
        resp = self._http.delete(path)
        self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> Any:
        log.debug("http.response", status=resp.status_code, url=str(resp.url))

        if resp.status_code == 401:
            raise AuthError("Gateway returned 401 — session is not authenticated.")

        if resp.status_code == 429:
            raise RateLimitError(
                f"Rate limit exceeded (HTTP 429). Retry-After: "
                f"{resp.headers.get('Retry-After', 'unknown')}"
            )

        if resp.status_code == 400:
            body = self._safe_json(resp)
            msg = body.get("error", resp.text) if isinstance(body, dict) else resp.text
            raise OrderRejectedError(f"Order rejected (400): {msg}")

        if resp.status_code >= 500:
            raise GatewayError(
                f"Gateway error {resp.status_code}: {resp.text[:200]}",
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
        self._http.close()

    def __enter__(self) -> "IBKRClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
