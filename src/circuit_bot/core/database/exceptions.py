from typing import Any

from ..exception import CircuitException

__all__ = ("DatabaseError", "DatabaseNotConnected", "RecordNotFoundError", "RepositoryError")


class DatabaseError(CircuitException):
    """Raised when a database operation fails."""


class DatabaseNotConnected(DatabaseError):
    """Raised when a database operation is attempted before connecting."""


class RepositoryError(DatabaseError):
    """Base exception for repository-layer errors."""


class RecordNotFoundError(RepositoryError):
    """Raised when a single-record lookup finds nothing.

    Carries the table and key the lookup used, so whoever handles the error
    can say what kind of record is gone without parsing the message.
    """

    def __init__(self, table: str, key: str, value: Any):
        self.table = table
        self.key = key
        self.value = value

        super().__init__(f"{table}.{key}={value!r} not found")
