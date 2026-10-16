from ..api.exceptions import APIError, APIRateLimitError, APIResponseError, APIUnavailableError

__all__ = ("BloxlinkAPIError", "BloxlinkError", "BloxlinkRateLimitError", "BloxlinkUnavailableError")


class BloxlinkError(APIError):
    """Raised when the Bloxlink API cannot be reached."""


class BloxlinkAPIError(BloxlinkError, APIResponseError):
    """Raised when the Bloxlink API returns an unexpected or malformed response."""


class BloxlinkUnavailableError(BloxlinkAPIError, APIUnavailableError):
    """Raised when Bloxlink answers with a server error (5xx).

    The API answered, so the service is reachable and its own backend is failing.
    """


class BloxlinkRateLimitError(BloxlinkError, APIRateLimitError):
    """Raised when a request is refused or rate limited.

    Covers both a 429 from Bloxlink and the client's own daily quota guard.
    ``retry_after`` is the seconds to wait when that is known - until the quota
    counter resets, or the delay Bloxlink asked for - and ``None`` otherwise.
    """
