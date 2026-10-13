from ..exception import CircuitException

__all__ = ("DatabaseError", "DatabaseNotConnected")


class DatabaseError(CircuitException):
    """Raised when a database operation fails."""


class DatabaseNotConnected(DatabaseError):
    """Raised when a database operation is attempted before connecting."""
