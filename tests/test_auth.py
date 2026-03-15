"""Unit tests for SessionManager (broker/auth.py)."""

import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from broker.auth import SessionManager, _TICKLE_INTERVAL_SECONDS
from broker.exceptions import AuthError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(settings, http_client: httpx.Client) -> SessionManager:
    return SessionManager(http=http_client, settings=settings)


# ---------------------------------------------------------------------------
# ensure_authenticated
# ---------------------------------------------------------------------------


@respx.mock
def test_ensure_authenticated_succeeds_when_already_authed(settings):
    respx.get(f"{settings.gateway_url}/iserver/auth/status").mock(
        return_value=httpx.Response(200, json={"authenticated": True, "connected": True})
    )
    http = httpx.Client(base_url=settings.gateway_url, verify=False)
    session = _make_session(settings, http)
    session.ensure_authenticated()  # should not raise


@respx.mock
def test_ensure_authenticated_reauths_and_succeeds(settings):
    status_route = respx.get(f"{settings.gateway_url}/iserver/auth/status")
    # First call: not authenticated; second call (after reauth): authenticated
    status_route.side_effect = [
        httpx.Response(200, json={"authenticated": False}),
        httpx.Response(200, json={"authenticated": True}),
    ]
    respx.post(f"{settings.gateway_url}/iserver/reauthenticate").mock(
        return_value=httpx.Response(200, json={"message": "triggered"})
    )

    http = httpx.Client(base_url=settings.gateway_url, verify=False)
    session = _make_session(settings, http)

    with patch("broker.auth._REAUTH_WAIT_SECONDS", 0):  # skip sleep in tests
        session.ensure_authenticated()  # should not raise


@respx.mock
def test_ensure_authenticated_raises_when_reauth_fails(settings):
    respx.get(f"{settings.gateway_url}/iserver/auth/status").mock(
        return_value=httpx.Response(200, json={"authenticated": False})
    )
    respx.post(f"{settings.gateway_url}/iserver/reauthenticate").mock(
        return_value=httpx.Response(200, json={"message": "triggered"})
    )

    http = httpx.Client(base_url=settings.gateway_url, verify=False)
    session = _make_session(settings, http)

    with patch("broker.auth._REAUTH_WAIT_SECONDS", 0):
        with pytest.raises(AuthError, match="not authenticated"):
            session.ensure_authenticated()


@respx.mock
def test_ensure_authenticated_handles_network_error(settings):
    respx.get(f"{settings.gateway_url}/iserver/auth/status").mock(
        side_effect=httpx.ConnectError("refused")
    )
    respx.post(f"{settings.gateway_url}/iserver/reauthenticate").mock(
        side_effect=httpx.ConnectError("refused")
    )

    http = httpx.Client(base_url=settings.gateway_url, verify=False)
    session = _make_session(settings, http)

    with patch("broker.auth._REAUTH_WAIT_SECONDS", 0):
        with pytest.raises(AuthError):
            session.ensure_authenticated()


# ---------------------------------------------------------------------------
# tickle
# ---------------------------------------------------------------------------


@respx.mock
def test_tickle_calls_correct_endpoint(settings):
    tickle_url = settings.gateway_url.rstrip("/") + "/tickle"
    respx.post(tickle_url).mock(return_value=httpx.Response(200, json={}))

    http = httpx.Client(base_url=settings.gateway_url, verify=False)
    session = _make_session(settings, http)
    session.tickle()  # should not raise

    assert respx.calls.call_count == 1


@respx.mock
def test_tickle_does_not_raise_on_failure(settings):
    """Tickle failures should be logged and swallowed, not propagate."""
    tickle_url = settings.gateway_url.rstrip("/") + "/tickle"
    respx.post(tickle_url).mock(side_effect=httpx.ConnectError("refused"))

    http = httpx.Client(base_url=settings.gateway_url, verify=False)
    session = _make_session(settings, http)
    session.tickle()  # must not raise


# ---------------------------------------------------------------------------
# keepalive thread
# ---------------------------------------------------------------------------


def test_start_stop_keepalive_is_idempotent(settings):
    http = MagicMock(spec=httpx.Client)
    session = _make_session(settings, http)

    session.start_keepalive()
    assert session._keepalive_thread is not None
    assert session._keepalive_thread.is_alive()

    # Calling start again should not spawn a second thread
    first_thread = session._keepalive_thread
    session.start_keepalive()
    assert session._keepalive_thread is first_thread

    session.stop_keepalive()
    assert not session._keepalive_thread.is_alive()


def test_keepalive_thread_is_daemon(settings):
    http = MagicMock(spec=httpx.Client)
    session = _make_session(settings, http)
    session.start_keepalive()
    assert session._keepalive_thread.daemon is True
    session.stop_keepalive()


def test_keepalive_calls_tickle(settings):
    """Verify tickle is invoked by the keepalive loop."""
    http = MagicMock(spec=httpx.Client)
    session = _make_session(settings, http)

    tickle_called = threading.Event()
    original_tickle = session.tickle

    def patched_tickle():
        tickle_called.set()
        original_tickle()

    session.tickle = patched_tickle

    with patch("broker.auth._TICKLE_INTERVAL_SECONDS", 0.05):
        session.start_keepalive()
        assert tickle_called.wait(timeout=2), "tickle was not called within 2s"
        session.stop_keepalive()


# ---------------------------------------------------------------------------
# _gateway_host
# ---------------------------------------------------------------------------


def test_gateway_host_extraction(settings):
    http = MagicMock(spec=httpx.Client)
    session = _make_session(settings, http)
    assert session._gateway_host() == "localhost:5000"
