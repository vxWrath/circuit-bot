from ..exception import CircuitException

__all__ = ("RobloxAPIError", "RobloxError")


class RobloxError(CircuitException):
    """Raised when the Roblox API cannot be reached."""


class RobloxAPIError(RobloxError):
    """Raised when the Roblox API returns an unexpected or malformed response."""
