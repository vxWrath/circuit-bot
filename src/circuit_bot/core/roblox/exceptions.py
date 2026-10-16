from ..api.exceptions import APIError, APIRateLimitError, APIResponseError, APIUnavailableError

__all__ = ("RobloxAPIError", "RobloxError", "RobloxRateLimitError", "RobloxUnavailableError")


class RobloxError(APIError):
    """Raised when the Roblox API cannot be reached."""


class RobloxAPIError(RobloxError, APIResponseError):
    """Raised when the Roblox API returns an unexpected or malformed response."""


class RobloxUnavailableError(RobloxAPIError, APIUnavailableError):
    """Raised when Roblox answers with a server error (5xx).

    The API answered, so this is not a request that never got through.
    """


class RobloxRateLimitError(RobloxError, APIRateLimitError):
    """Raised when a request is rate limited and not worth waiting out in-line.

    ``retry_after`` is the delay Roblox asked for in seconds, or ``None`` when
    nothing said - never a value the client invented.
    """
