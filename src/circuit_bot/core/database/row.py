import re

from ..model import Model

__all__ = ("Row", "to_table_name")

# Splits PascalCase into words while keeping acronyms whole, so RobloxIDRow
# becomes "Roblox_ID" and HTTPLogRow "HTTP_Log" rather than one word per capital.
PASCAL_TO_SNAKE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z0-9])(?=[A-Z])")


def to_table_name(cls: type) -> str:
    """Convert a Row class name to a table name: ``PlayerRow`` -> ``player``."""
    return PASCAL_TO_SNAKE.sub("_", cls.__name__.removesuffix("Row")).lower()


class Row(Model):
    """Base class for every database row.

    A row is a :class:`~circuit_bot.core.model.Model` whose fields mirror table
    columns, in the column names Postgres uses.

    A table is described once, by a field-less base class whose ``Meta`` names
    it, and the shapes used to read and write it inherit from that - so the
    table's name, its primary key and the columns the database fills in live in
    one place, however many shapes it has::

        class PlayerTable(Row):
            class Meta:
                abstract = True
                table = "players"
                primary_key = "snowflake"
                generated = ("created_at",)

        class PlayerRow(PlayerTable):     # the full row
            snowflake: int
            name: str
            created_at: datetime

        class NewPlayer(PlayerTable):     # the shape an insert sends
            snowflake: int
            name: str

    ``Meta`` is the only home for all three, and it is never copied onto the
    class: a shape reads the one its base declared by ordinary attribute lookup,
    so there is nothing to keep in step.  Which also means a shape that declares
    no ``Meta`` of its own - the usual case - is described entirely by its base.

    ``abstract`` is read from the class declaring it and never inherited, so a
    table base marks itself abstract without marking every shape under it.  Use
    it for a class that isn't a shape at all: a table base, or a base holding
    fields several rows share.

    A subclass can only ever *add* fields, never drop them, which is why the
    table class itself holds none: put a column on it and every shape inherits
    that column.  A shape with no ``Meta`` above it derives its table from its
    class name (``PlayerRow`` -> ``player``), keys on ``id``, and has no
    generated columns.
    """
