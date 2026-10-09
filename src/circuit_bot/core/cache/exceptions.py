from ..exception import CircuitException

__all__ = ("CacheError", "CacheNotConnected", "CacheValidationError")


class CacheError(CircuitException):
    """Raised when a cache operation fails."""


class CacheNotConnected(CacheError):
    """Raised when a cache operation is attempted before connecting."""


class CacheValidationError(CacheError):
    """Raised when a value found in the cache can't be decoded into the requested type.

    The bad entry is left in Redis, so the caller decides whether to refill or
    delete it.
    """
