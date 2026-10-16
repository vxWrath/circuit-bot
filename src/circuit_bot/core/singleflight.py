"""Letting callers that ask the same thing at once share one answer."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, cast

__all__ = ("SingleFlight",)


class SingleFlight:
    """Run one coroutine for every caller asking the same thing at once.

    Later callers wait on the call already running and get its result or its
    exception; a failure is not remembered, and nothing is kept once the call
    finishes.  Callers wait *on* the call's own task, so cancelling one never
    cancels what the others are waiting on, and *func* sees the first caller's
    arguments.  Keys must be hashable and equal for two requests that mean the
    same thing.
    """

    def __init__(self) -> None:
        self._in_flight: dict[Any, asyncio.Task[Any]] = {}

    async def run[T](self, key: Any, func: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: Any) -> T:
        """Call ``func(*args, **kwargs)`` once per *key*, sharing what it returns.

        Every caller arriving while the call is in flight gets the same result, or
        the same exception.  *key* must be hashable and equal for two requests that
        mean the same thing.
        """

        task = self._in_flight.get(key)

        if task is None:
            task = asyncio.create_task(func(*args, **kwargs))
            self._in_flight[key] = task
            task.add_done_callback(lambda done: self.forget(key, done))

        # Wait *on* the call, not by awaiting it: awaiting a task cancels through
        # it, so one caller walking away would cancel what the others are waiting
        # for.  ``asyncio.shield`` reports the abandoned call's failure to the
        # loop instead, which the waiting callers are about to be handed anyway.
        await asyncio.wait({task})

        # The entry is keyed by something the type checker can't tie back to T,
        # so what a lookup hands back is spelled out here.
        return cast("T", task.result())

    def forget(self, key: Any, task: asyncio.Task[Any]) -> None:
        """Drop *key*'s entry now that *task* is done, and read its exception.

        The callback :meth:`run` leaves on the task it started.  Reading the
        exception is what stops asyncio warning about one nobody waited for.
        """

        self._in_flight.pop(key, None)

        if not task.cancelled():
            task.exception()
