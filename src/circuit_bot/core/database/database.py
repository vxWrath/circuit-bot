from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Literal

import asyncpg

if TYPE_CHECKING:
    from .connection import DatabaseConnection
    from .repository import Repository
    from .row import Row

__all__ = ("Database",)

IsolationLevel = Literal[
    "read_committed",
    "repeatable_read",
    "serializable",
    "read_uncommitted",
]


class Database(ABC):
    """A scope statements run in: the pool, or a connection checked out of it.

    Which scope a :class:`Repository` was built on is the only thing deciding
    where its statements land.  The scope members are abstract, so a subclass
    that drops one fails at construction rather than silently answering ``None``.
    """

    @abstractmethod
    def acquire(self) -> AbstractAsyncContextManager["DatabaseConnection"]:
        """The connection this scope's statements run on, for the block."""
        ...

    @abstractmethod
    def transaction(
        self,
        *,
        isolation: IsolationLevel = "read_committed",
        readonly: bool = False,
        deferrable: bool = False,
    ) -> AbstractAsyncContextManager["DatabaseConnection"]:
        """A transaction on that connection, yielding it for the block."""
        ...

    @abstractmethod
    def get_repository[R: Row](self, row: type[R]) -> "Repository[R]":
        """The repository for *row*, whose statements run in this scope."""
        ...

    # -- statements --------------------------------------------------------

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[asyncpg.Record]:
        """Run *query* and return every row it produced, or an empty list if none."""

        async with self.acquire() as conn:
            return await conn.raw.fetch(query, *args, timeout=timeout)

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> asyncpg.Record | None:
        """Run *query* and return its first row, or ``None`` if it produced none.

        Further rows are discarded, so pair it with ``LIMIT 1`` or an ``ORDER BY``
        when the result set can hold more than one.
        """

        async with self.acquire() as conn:
            return await conn.raw.fetchrow(query, *args, timeout=timeout)

    async def fetchval(self, query: str, *args: Any, column: int = 0, timeout: float | None = None) -> Any:
        """Run *query* and return the first column of its first row, or ``None``.

        *column* picks a column by position, for queries selecting more than one.
        """

        async with self.acquire() as conn:
            return await conn.raw.fetchval(query, *args, column=column, timeout=timeout)

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        """Run *query* and return its status string, such as ``"UPDATE 3"``.

        *query* may hold several semicolon-separated statements, and the status is
        then the last one's.  :meth:`executemany` runs one statement over many
        argument sets; :meth:`copy_records_to_table` loads rows in bulk.
        """

        async with self.acquire() as conn:
            return await conn.raw.execute(query, *args, timeout=timeout)

    async def executemany(self, query: str, args: Sequence[Sequence[Any]], timeout: float | None = None) -> None:
        """Run *query* once for each argument set in *args*.

        Every set runs in order, but not as a transaction - wrap the call in
        :meth:`transaction` when the batch has to land all at once.
        """

        async with self.acquire() as conn:
            await conn.raw.executemany(query, args, timeout=timeout)

    async def copy_records_to_table(
        self,
        table: str,
        records: Sequence[Sequence[Any]],
        columns: Sequence[str],
        *,
        schema_name: str | None = None,
        where: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Load *records* into *table* with binary ``COPY``, returning its status string.

        *records* holds one sequence per row, positioned to match *columns*, and
        *schema_name* qualifies the table when it isn't on the search path.
        *where* filters rows server-side (PostgreSQL 12+).  It is one statement, so
        a rejected row fails the whole load and ``ON CONFLICT`` isn't available.
        """

        async with self.acquire() as conn:
            return await conn.raw.copy_records_to_table(
                table,
                records=records,
                columns=columns,
                schema_name=schema_name,
                where=where,
                timeout=timeout,
            )
