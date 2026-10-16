from ..exception import CircuitException

__all__ = ("APIError", "APIRateLimitError", "APIResponseError", "APIUnavailableError")


class APIError(CircuitException):
    """Raised when a third-party API cannot be reached."""


class APIResponseError(APIError):
    """Raised when an API returns an unexpected or malformed response."""


class APIUnavailableError(APIResponseError):
    """Raised when an API answers with a server error (5xx).

    The API answered, so this isn't a request that never got through - the
    service is reachable but its backend is failing.
    """


class APIRateLimitError(APIError):
    """Raised when a request is refused or rate limited.

    ``retry_after`` is how many seconds to wait when that is actually known - the
    delay the API asked for, or the seconds until a local quota resets - and
    ``None`` when nothing said, never a value the client invented.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
