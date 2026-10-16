"""Letting callers that ask the same thing at once share one answer."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, cast

__all__ = ("SingleFlight",)


class SingleFlight:
    """Run one coroutine for every caller asking the same thing at once.

    A cache answers "did we learn this earlier?" and has nothing to say while
    the first request for something is still out - so every caller that arrives
    in that window does the work again.  This is what covers that window: the
    first caller starts the call, later ones wait on the one already running,
    and all of them get its answer.

    The two mechanisms are neighbours and are meant to be used together, each
    covering the window the other can't: this one while an answer is being
    fetched, the cache once it exists.  Nothing is kept here after the call
    finishes - what happens to the answer next is the caller's to decide, which
    is what keeps this a coordinator rather than a second cache.

    A key is whatever makes two requests the same question - an API resource, a
    part of a profile - and each object that needs this holds its own, so one
    object's keys can't collide with another's.  Waits are per key, so two
    different questions never block each other.

    Four things it has to get right, and does:

    - **A caller's cancellation is not the call's.**  The call runs as a task
      of its own and callers wait *on* it rather than awaiting it directly, so
      an interaction timing out stops that caller waiting instead of cancelling
      the fetch everybody else is waiting on.
    - **A failure is not remembered.**  An exception reaches every waiter at
      once, and the entry goes with it, so the next caller starts a fresh call
      instead of being handed a stale failure.
    - **An exception nobody waited for is still read.**  When every waiter has
      gone, something has to ask the task for it, or asyncio reports it as
      never retrieved.
    - **An entry lives exactly as long as the call does**, whatever way the
      call ends.

    What the call itself sees is the first caller's: *func* is handed the
    arguments that caller gave, not a mixture of everyone's, so a per-caller
    argument - a callback reporting a wait - belongs to whoever started the
    call and not to those who joined it.
    """

    def __init__(self) -> None:
        self._in_flight: dict[Any, asyncio.Task[Any]] = {}

    async def run[T](self, key: Any, func: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: Any) -> T:
        """Call ``func(*args, **kwargs)`` once per *key*, sharing what it returns.

        Every caller that arrives while the call is in flight gets the same
        result - or the same exception, if that is how it ended.  The call is
        taken as a function and its arguments rather than as a coroutine
        object, because one of those can only be awaited once and this may be
        starting it on behalf of whoever arrived first.

        *key* is compared and never stored beyond the call, so it has to be
        hashable and equal for two requests that mean the same thing.
        """

        task = self._in_flight.get(key)

        if task is None:
            task = asyncio.create_task(func(*args, **kwargs))
            self._in_flight[key] = task
            task.add_done_callback(lambda done: self.forget(key, done))

        # Waiting *on* the call rather than awaiting it: a coroutine that awaits
        # a task is cancelled through it, and one caller walking away must not
        # cancel what the others are waiting on.  ``asyncio.wait`` never
        # cancels what it waits for, where ``asyncio.shield`` would report the
        # abandoned call's failure to the loop - noise for a failure the
        # waiting callers are about to be handed anyway.
        await asyncio.wait({task})

        # The entry is keyed by something the type checker can't tie back to T,
        # so what a lookup hands back is spelled out here.
        return cast("T", task.result())

    def forget(self, key: Any, task: asyncio.Task[Any]) -> None:
        """Drop *key*'s entry now that *task* is done, and read its exception.

        The callback :meth:`run` leaves on the task it started.  Reading the
        exception is what stops asyncio warning about one nobody waited for:
        every waiter may have been cancelled before it finished, and then this
        is the only thing that looks.
        """

        self._in_flight.pop(key, None)

        if not task.cancelled():
            task.exception()
