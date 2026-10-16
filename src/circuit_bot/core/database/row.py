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
    """Base class for every database row: a :class:`~circuit_bot.core.model.Model`
    whose fields mirror table columns by Postgres name.

    A table is described once by a field-less base whose ``Meta`` names it
    (``table``, ``primary_key``, ``generated``); shapes inherit that and can only
    add fields.  ``abstract`` is read from the declaring class, never inherited.
    With no ``Meta`` above it a shape names its table from its class name, keys
    on ``id`` and has no generated columns.
    """
