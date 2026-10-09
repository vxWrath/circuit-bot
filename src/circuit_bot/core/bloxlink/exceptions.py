from ..exception import CircuitException

__all__ = ("BloxlinkAPIError", "BloxlinkError")


class BloxlinkError(CircuitException):
    """Raised when the Bloxlink API cannot be reached."""


class BloxlinkAPIError(BloxlinkError):
    """Raised when the Bloxlink API returns an unexpected or malformed response."""
