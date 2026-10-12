from ..exception import CircuitException

__all__ = ("BloxlinkAPIError", "BloxlinkError", "BloxlinkRateLimitError")


class BloxlinkError(CircuitException):
    """Raised when the Bloxlink API cannot be reached."""


class BloxlinkAPIError(BloxlinkError):
    """Raised when the Bloxlink API returns an unexpected or malformed response."""


class BloxlinkRateLimitError(BloxlinkError):
    """Raised when a request is refused because the daily quota is spent.

    Raised by the client itself, before anything reaches Bloxlink, but a
    subclass of :class:`BloxlinkError` so a caller handling that covers it too.
    """
