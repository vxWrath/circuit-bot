import datetime
import decimal
import uuid
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

import asyncpg
import msgspec

from .exceptions import RecordNotFoundError, RepositoryError
from .row import Row, to_table_name

if TYPE_CHECKING:
    from .database import Database

__all__ = ("RecordNotFoundError", "Repository", "RepositoryError")

# What asyncpg wants handed to it as it is.  A row's values go straight into a
# statement, so a ``datetime`` has to stay a ``datetime`` and a ``Decimal`` a
# ``Decimal``.  ``msgspec.to_builtins`` is what recurses into a model - which is
# what lets a nested one be stored at all - and these are the types it must
# leave alone on the way down, rather than rendering them the way JSON would.
POSTGRES_TYPES = (datetime.datetime, datetime.date, datetime.time, decimal.Decimal, uuid.UUID, bytes)


class Repository[R: Row]:
    """CRUD for one shape over one table.

    A repository is bound to a row shape: reads come back as that shape, and
    writes send exactly the fields it declares, minus the ones the database
    fills in (the ``generated`` list in the ``Meta`` its table base declared).
    Asking for another shape over the same table gives another repository, which
    is the point of ``Row`` subclasses::

        players = database.get_repository(PlayerRow)       # reads the full row
        new_players = database.get_repository(NewPlayer)   # writes just what's new

        player = await players.get_by_pk_or_raise(snowflake)
        created = await new_players.insert(NewPlayer(snowflake=snowflake, name="kyle"))

    Where a repository's statements land is settled once, by what it was built
    on.  Built on the pool, each statement checks a connection out for itself;
    built on a connection (:meth:`DatabaseConnection.get_repository`), they all
    run on that one, inside whatever transaction it has open::

        async with database.transaction() as conn:
            await conn.get_repository(PlayerRow).update(player, name="kyle")
            await conn.get_repository(RosterRow).insert(RosterRow(snowflake=snowflake))

    Nothing here takes a connection, which is the point: there is no
    per-statement knob to forget, so a write can't quietly commit outside the
    transaction its neighbours are in.
    """

    def __init__(self, database: "Database", *, row_cls: type[R]) -> None:
        if not (isinstance(row_cls, type) and issubclass(row_cls, Row)):
            raise RepositoryError(f"{row_cls!r} is not a Row subclass")

        self.database = database
        self.row_cls = row_cls

        # `abstract` is the one Meta field that isn't inherited, so a table base
        # is rejected without rejecting the shapes under it.
        own_meta = row_cls.__dict__.get("Meta")

        if own_meta is not None and getattr(own_meta, "abstract", False):
            raise RepositoryError(f"{row_cls.__name__} is a table base, not a shape to read")

        fields = [f.name for f in msgspec.structs.fields(row_cls)]

        if not fields:
            raise RepositoryError(f"{row_cls.__name__} has no fields - a table base is not a shape to read")

        meta = getattr(row_cls, "Meta", None)

        self.table_name: str = getattr(meta, "table", None) or to_table_name(row_cls)
        self.primary_key_name: str = getattr(meta, "primary_key", "id")

        self._fields = fields
        self._generated: tuple[str, ...] = getattr(meta, "generated", ())

    # -- statement building -------------------------------------------------

    def check_columns(self, columns: Iterable[str]) -> None:
        """Reject columns the shape doesn't declare, before they reach the SQL."""

        unknown = [column for column in columns if column not in self._fields]

        if unknown:
            raise RepositoryError(
                f"{unknown[0]!r} is not a field of {self.row_cls.__name__} ({', '.join(self._fields)})"
            )

    def where_clause(self, filters: dict[str, Any]) -> str:
        """Render the equality ``WHERE`` clause over the columns in *filters*.

        The values are passed separately, in the same order, so the clause only
        ever holds column names - which is why the check happens here.
        """

        self.check_columns(filters)
        return " AND ".join(f"{column} = ${i}" for i, column in enumerate(filters, start=1))

    def column_values(self, obj: R, columns: Sequence[str]) -> list[Any]:
        """The values *columns* take on *obj*, in the order they were given.

        The repository's shape decides which columns a write sends; *obj* only
        has to carry them.  One that doesn't is a bug in the caller, so it fails
        here, named, rather than as a missing key inside the statement.
        """

        values = msgspec.to_builtins(obj, builtin_types=POSTGRES_TYPES)
        missing = [column for column in columns if column not in values]

        if missing:
            raise RepositoryError(
                f"{type(obj).__name__} has no {missing[0]!r} - "
                f"{self.row_cls.__name__} writes ({', '.join(self._fields)})"
            )

        return [values[column] for column in columns]

    # -- conversion helpers --------------------------------------------

    def from_record(self, record: asyncpg.Record | None) -> R | None:
        if record is None:
            return None
        return msgspec.convert(dict(record), type=self.row_cls)

    def from_records(self, records: Sequence[asyncpg.Record]) -> list[R]:
        return [msgspec.convert(dict(record), type=self.row_cls) for record in records]

    # -- reads -----------------------------------------------------------

    async def get_by_pk(self, primary_key: Any) -> R | None:
        query = f"SELECT * FROM {self.table_name} WHERE {self.primary_key_name} = $1"

        async with self.database.acquire() as conn:
            record = await conn.fetchrow(query, primary_key)

        return self.from_record(record)

    async def get_by_pk_or_raise(self, primary_key: Any) -> R:
        result = await self.get_by_pk(primary_key)

        if result is None:
            raise RecordNotFoundError(f"{self.table_name}.{self.primary_key_name}={primary_key!r} not found")

        return result

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> list[R]:
        query = f"SELECT * FROM {self.table_name} ORDER BY {self.primary_key_name} LIMIT $1 OFFSET $2"

        async with self.database.acquire() as conn:
            records = await conn.fetch(query, limit, offset)

        return self.from_records(records)

    async def find_by(self, **filters: Any) -> list[R]:
        """Simple equality-filter lookup, e.g. repo.find_by(user_id=5, active=True)."""
        if not filters:
            raise RepositoryError("find_by() called with no filters")

        query = f"SELECT * FROM {self.table_name} WHERE {self.where_clause(filters)}"

        async with self.database.acquire() as conn:
            records = await conn.fetch(query, *filters.values())

        return self.from_records(records)

    async def count(self, **filters: Any) -> int:
        where = f" WHERE {self.where_clause(filters)}" if filters else ""
        query = f"SELECT COUNT(*) FROM {self.table_name}{where}"

        async with self.database.acquire() as conn:
            return await conn.fetchval(query, *filters.values())

    # -- writes ----------------------------------------------------------

    async def insert(self, obj: R) -> R:
        """Insert *obj*, leaving out the columns the database fills in.

        The columns written are the ones the shape declares, minus
        ``generated``, so a shape can carry a generated column to read it
        back without ever writing it.  Returns the row ``RETURNING`` produced,
        which is the only thing ``INSERT ... RETURNING`` can do - so a missing
        row is an error rather than a ``None`` the caller has to handle.
        """

        columns = [field for field in self._fields if field not in self._generated]
        values = self.column_values(obj, columns)

        placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
        query = f"INSERT INTO {self.table_name} ({', '.join(columns)}) VALUES ({placeholders}) RETURNING *"

        async with self.database.acquire() as conn:
            record = await conn.fetchrow(query, *values)

        created = self.from_record(record)

        if created is None:
            raise RepositoryError(f"INSERT INTO {self.table_name} returned no row")

        return created

    async def update(self, obj: R, **changes: Any) -> R | None:
        """Partial update - only touches the fields you pass, and only reads back those.

        The row to change is *obj*'s, looked up by the primary key its table's
        ``Meta`` declares, and the model that comes back is *obj* with the
        columns the statement wrote replaced by whatever the database made of
        them - ``RETURNING`` names just those columns, so a value a default or a
        trigger changed comes back as the database left it::

            player = await players.update(player, name="kyle")

        ``None`` means no row matched, which is a row that was deleted rather
        than an error the caller can't act on.
        """

        if not changes:
            raise RepositoryError("update() called with no fields to change")

        self.check_columns(changes)

        try:
            primary_key = getattr(obj, self.primary_key_name)
        except AttributeError:
            raise RepositoryError(
                f"{type(obj).__name__} has no {self.primary_key_name!r} to look the row up by"
            ) from None

        set_clause = ", ".join(f"{column} = ${i}" for i, column in enumerate(changes, start=2))
        query = f"UPDATE {self.table_name} SET {set_clause} WHERE {self.primary_key_name} = $1 RETURNING {', '.join(changes)}"

        async with self.database.acquire() as conn:
            record = await conn.fetchrow(query, primary_key, *changes.values())

        if record is None:
            return None

        return msgspec.structs.replace(obj, **record)

    async def delete(self, primary_key: Any) -> bool:
        query = f"DELETE FROM {self.table_name} WHERE {self.primary_key_name} = $1"

        async with self.database.acquire() as conn:
            result = await conn.execute(query, primary_key)

        # asyncpg execute() returns e.g. "DELETE 1"
        return result.split()[-1] != "0"

    # -- bulk ops -----------------------------------------------------

    async def bulk_insert(self, objs: Sequence[R]) -> None:
        """Fastest path for large batches - uses COPY, no RETURNING support."""

        if not objs:
            return

        columns = [field for field in self._fields if field not in self._generated]
        records = [self.column_values(obj, columns) for obj in objs]

        async with self.database.acquire() as conn:
            await conn.copy_records_to_table(self.table_name, records=records, columns=columns)
