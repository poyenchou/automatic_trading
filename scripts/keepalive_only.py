"""
Standalone script to check and maintain the IBKR gateway session.
Useful for manual testing before running the full morning workflow.

Usage:
    python scripts/keepalive_only.py
"""

import signal
import sys
import time

import httpx
import structlog

sys.path.insert(0, ".")

from broker.auth import SessionManager
from config.settings import Settings
from logging_config.setup import configure_logging

log = structlog.get_logger(__name__)


def main() -> None:
    settings = Settings()
    configure_logging(level=settings.log_level, fmt="console")

    log.info("keepalive.start", gateway=settings.gateway_url)

    http = httpx.Client(verify=settings.gateway_verify_ssl, timeout=15)
    session = SessionManager(http=http, settings=settings)

    try:
        session.ensure_authenticated()
        log.info("keepalive.authenticated")
    except Exception as exc:
        log.error("keepalive.auth_failed", error=str(exc))
        sys.exit(1)

    session.start_keepalive()
    log.info("keepalive.running", tip="Press Ctrl+C to stop")

    def _shutdown(sig, frame):  # noqa: ANN001
        log.info("keepalive.shutdown")
        session.stop_keepalive()
        http.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
