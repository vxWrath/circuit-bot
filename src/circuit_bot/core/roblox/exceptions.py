from ..api.exceptions import APIError, APIRateLimitError, APIResponseError, APIUnavailableError

__all__ = ("RobloxAPIError", "RobloxError", "RobloxRateLimitError", "RobloxUnavailableError")


class RobloxError(APIError):
    """Raised when the Roblox API cannot be reached."""


class RobloxAPIError(RobloxError, APIResponseError):
    """Raised when the Roblox API returns an unexpected or malformed response."""


class RobloxUnavailableError(RobloxAPIError, APIUnavailableError):
    """Raised when Roblox answers with a server error (5xx).

    The API answered, so this isn't a request that never got through: Roblox is
    reachable but its backend is failing, which is Roblox's problem and usually
    passes on its own.
    """


class RobloxRateLimitError(RobloxError, APIRateLimitError):
    """Raised when a request is rate limited and not worth waiting out in-line.

    ``retry_after`` is how many seconds to wait before trying again when that
    is actually known - the delay Roblox asked for, which is where it will
    almost always come from.  It's ``None`` when nothing said, so a caller
    shouldn't read it as a value the client invented.
    """
