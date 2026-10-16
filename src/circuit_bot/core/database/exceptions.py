from ..exception import CircuitException

__all__ = ("DatabaseError", "DatabaseNotConnected", "RecordNotFoundError", "RepositoryError")


class DatabaseError(CircuitException):
    """Raised when a database operation fails."""


class DatabaseNotConnected(DatabaseError):
    """Raised when a database operation is attempted before connecting."""


class RepositoryError(DatabaseError):
    """Base exception for repository-layer errors."""


class RecordNotFoundError(RepositoryError):
    """Raised when a single-record lookup finds nothing."""
