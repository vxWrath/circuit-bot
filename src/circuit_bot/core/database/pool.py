from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("DatabasePool",)


class DatabasePool:
    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot
        self._pool: asyncpg.Pool | None = None
        self._connection: asyncpg.Connection | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("DatabasePool has not been initialized.")
        return self._pool

    @property
    def connection(self) -> asyncpg.Connection:
        if not self._connection:
            raise RuntimeError("DatabasePool has not been initialized.")
        return self._connection
