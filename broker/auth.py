"""
SessionManager — handles IBKR Client Portal Gateway authentication and keepalive.

The gateway uses a browser-based OAuth session that expires after ~15 minutes
of inactivity. SessionManager keeps it alive with a background tickle thread
and provides guard methods called before each workflow step.
"""

import threading
import time

import httpx
import structlog

from broker.exceptions import AuthError
from config.settings import Settings

log = structlog.get_logger(__name__)

_TICKLE_INTERVAL_SECONDS = 300  # 5 minutes — well within the 15-min timeout
_REAUTH_WAIT_SECONDS = 3


class SessionManager:
    def __init__(self, http: httpx.Client, settings: Settings) -> None:
        self._http = http
        self._settings = settings
        self._stop_event = threading.Event()
        self._keepalive_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_authenticated(self) -> None:
        """
        Check auth status. If not authenticated, attempt reauthentication once.
        Raises AuthError if the session cannot be recovered programmatically
        (user must open the gateway browser UI).
        """
        status = self._get_auth_status()
        if status:
            log.debug("session.authenticated")
            return

        log.warning("session.not_authenticated", action="attempting_reauth")
        self.reauthenticate()
        time.sleep(_REAUTH_WAIT_SECONDS)

        if not self._get_auth_status():
            raise AuthError(
                "Gateway session is not authenticated. "
                f"Open https://{self._gateway_host()} in a browser, log in, "
                "then restart the bot."
            )
        log.info("session.reauth_succeeded")

    def tickle(self) -> None:
        """POST /tickle to reset the 15-minute inactivity timer."""
        try:
            resp = self._http.post(
                f"{self._settings.gateway_url.rstrip('/')}/tickle",
                timeout=10,
            )
            resp.raise_for_status()
            log.debug("session.tickled")
        except Exception as exc:
            log.warning("session.tickle_failed", error=str(exc))

    def reauthenticate(self) -> None:
        """POST /iserver/reauthenticate — works without a browser if the session
        is merely stale rather than fully logged out."""
        try:
            resp = self._http.post(
                f"{self._settings.gateway_url}/iserver/reauthenticate",
                timeout=10,
            )
            resp.raise_for_status()
            log.info("session.reauthenticate_requested")
        except Exception as exc:
            log.warning("session.reauthenticate_failed", error=str(exc))

    def start_keepalive(self) -> None:
        """Start a daemon thread that tickles the session every 5 minutes."""
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            return
        self._stop_event.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name="ibkr-keepalive",
            daemon=True,
        )
        self._keepalive_thread.start()
        log.info("session.keepalive_started", interval_s=_TICKLE_INTERVAL_SECONDS)

    def stop_keepalive(self) -> None:
        """Signal the keepalive thread to exit and wait for it."""
        self._stop_event.set()
        if self._keepalive_thread:
            self._keepalive_thread.join(timeout=5)
        log.info("session.keepalive_stopped")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_auth_status(self) -> bool:
        try:
            resp = self._http.get(
                f"{self._settings.gateway_url}/iserver/auth/status",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("authenticated", False))
        except Exception as exc:
            log.warning("session.auth_status_error", error=str(exc))
            return False

    def _keepalive_loop(self) -> None:
        while not self._stop_event.wait(timeout=_TICKLE_INTERVAL_SECONDS):
            self.tickle()

    def _gateway_host(self) -> str:
        # e.g. "localhost:5000" from "https://localhost:5000/v1/api"
        url = self._settings.gateway_url
        return url.split("//")[-1].split("/")[0]
