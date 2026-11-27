"""The failures only this module can explain."""

from typing import TYPE_CHECKING, Any

import asyncpg

from ...core.bluerobot.formatter import ErrorFormatter, format_fail
from ...core.exception import CircuitException

if TYPE_CHECKING:
    from ...core.bluerobot import Interaction

__all__ = ("TeamError", "TeamErrorFormatter", "TeamUniqueViolationFormatter", "TeamWithoutRole")


class TeamError(CircuitException):
    """A team rule the service refused, with the reply as the message."""


class TeamWithoutRole(TeamError):
    """A team that has no Discord role.  Every team needs one, so callers raise instead of guessing."""


class TeamErrorFormatter(ErrorFormatter):
    """The reply for a team rule the service refused.

    The failure is the invoker's, not the bot's, so nothing is reported to
    the error webhook.
    """

    def format(self, _: "Interaction", error: Exception) -> str | None:
        if not isinstance(error, TeamError):
            return None

        return format_fail(str(error))


class TeamUniqueViolationFormatter(ErrorFormatter):
    """The reply for a team write that hit a unique constraint.

    The reply comes from the constraint's name, so an unknown constraint
    stays unhandled and reported rather than guessed at.  The command
    pre-checks the common case, so a violation here means two submissions
    raced - the reply is neutral, and nothing is reported.
    """

    def format(self, _: "Interaction", error: Exception) -> str | None:
        if not isinstance(error, asyncpg.UniqueViolationError):
            return None

        as_dict: dict[str, Any] = error.as_dict()

        constraint = as_dict["constraint_name"]

        if constraint == "team_applications_one_per_owner":
            return format_fail("You already have a pending team application.")

        if constraint == "team_applications_lower_name_key":
            return format_fail("That team name is already taken.")

        if constraint == "teams_previous_team_id_key":
            return format_fail("That team already has a successor.")

        return None
