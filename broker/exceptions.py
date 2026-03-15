class BrokerError(Exception):
    """Base class for all broker-related errors."""


class AuthError(BrokerError):
    """Session is not authenticated or authentication failed."""


class OrderRejectedError(BrokerError):
    """The gateway rejected an order submission."""

    def __init__(self, message: str, order_id: str | None = None) -> None:
        super().__init__(message)
        self.order_id = order_id


class RateLimitError(BrokerError):
    """Too many requests sent to the gateway."""


class GatewayError(BrokerError):
    """Unexpected HTTP error from the gateway (5xx)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
