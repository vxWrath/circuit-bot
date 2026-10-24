"""The replies for the failures only this module can explain."""

from typing import TYPE_CHECKING, Any

import asyncpg

from ...core.bluerobot.formatter import ErrorFormatter, format_fail

if TYPE_CHECKING:
    from ...core.bluerobot import Interaction

__all__ = ("PlayerUniqueViolationFormatter",)


class PlayerUniqueViolationFormatter(ErrorFormatter):
    """The reply for a link write that lost a race over the unique constraint.

    The commands' pre-checks catch the usual case; this owns the race itself,
    where the database refuses a write that already went to another member.
    The reply comes from the constraint's name, so an unknown constraint
    stays unhandled and reported rather than guessed at.  The race is also
    posted to the error webhook - the pre-check failed to stop two writers.
    """

    report = True

    def format(self, _: "Interaction", error: Exception) -> str | None:
        if not isinstance(error, asyncpg.UniqueViolationError):
            return None

        as_dict: dict[str, Any] = error.as_dict()

        if as_dict["constraint_name"] == "players_roblox_id_key":
            return format_fail("That Roblox account is already linked to another Discord account.")

        return None
