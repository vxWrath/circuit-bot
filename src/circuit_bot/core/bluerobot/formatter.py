import datetime
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

import discord

from .color import CommonEmojis

if TYPE_CHECKING:
    from .interaction import Interaction

__all__ = ("ErrorFormatter",)


def format_fail(text: str) -> str:
    """*text* in the failure frame every error reply uses."""

    return f"{CommonEmojis.FAIL} **{text}**"


def format_retry(retry_after: float | None) -> str:
    """A relative retry time - ``in 3 seconds`` - or ``later`` when it is unknown.

    One second is added so the truncated delay rounds up, never telling the
    user to try again a moment before the wait is actually over.
    """

    if retry_after is None:
        return "later"

    retry_at = discord.utils.utcnow() + datetime.timedelta(seconds=int(retry_after) + 1)
    return discord.utils.format_dt(retry_at, "R")


class ErrorFormatter(ABC):
    """One formatting step in :class:`~.errors.ErrorHandler`.

    :meth:`format` returns the reply for an error the formatter owns, and
    ``None`` for every other error, handing it to the next formatter.  Set
    ``report`` to also have an owned error posted to the error webhook after
    the reply, for failures that mean something to the operator as well.
    """

    report: ClassVar[bool] = False

    @abstractmethod
    def format(self, interaction: "Interaction", error: Exception) -> str | None:
        raise NotImplementedError
