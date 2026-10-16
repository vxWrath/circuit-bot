from ..api.exceptions import APIError, APIRateLimitError, APIResponseError, APIUnavailableError

__all__ = ("BloxlinkAPIError", "BloxlinkError", "BloxlinkRateLimitError", "BloxlinkUnavailableError")


class BloxlinkError(APIError):
    """Raised when the Bloxlink API cannot be reached."""


class BloxlinkAPIError(BloxlinkError, APIResponseError):
    """Raised when the Bloxlink API returns an unexpected or malformed response."""


class BloxlinkUnavailableError(BloxlinkAPIError, APIUnavailableError):
    """Raised when Bloxlink answers with a server error (5xx).

    The API answered, so this isn't a request that never got through: Bloxlink
    is reachable but its backend is failing, which is Bloxlink's problem and
    usually passes on its own.
    """


class BloxlinkRateLimitError(BloxlinkError, APIRateLimitError):
    """Raised when a request is refused or rate limited.

    Covers both a 429 from Bloxlink and the client's own daily quota guard,
    which refuses requests locally rather than spending an API key's goodwill.
    ``retry_after`` is how many seconds to wait before trying again when that
    is actually known: seconds until the counter resets for the daily quota,
    and the delay Bloxlink asked for on a 429 - though Bloxlink doesn't
    document sending one, so expect ``None`` there.
    """
