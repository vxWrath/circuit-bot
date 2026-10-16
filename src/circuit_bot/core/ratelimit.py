"""Waiting out API rate limits, shared by the API clients."""

import datetime
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

__all__ = ("OnRatelimit", "RetryPolicy", "backoff_delay", "retry_after", "retry_delay")

# Told how long a request is about to wait, just before it waits.
type OnRatelimit = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    """How long a client is willing to sit on a rate limit.

    *attempts* counts the first try, so the default of three allows two waits.
    A server asking for longer than *max_wait* seconds isn't waited on at all -
    the client raises instead, leaving the caller to decide whether to come
    back later.  When no header says how long to wait, *base_wait* is the first
    backoff step, doubling per attempt up to *max_backoff*.
    """

    attempts: int = 3
    max_wait: float = 10.0
    base_wait: float = 1.0
    max_backoff: float = 8.0


def retry_after(headers: Mapping[str, str]) -> float | None:
    """Read how many seconds *headers* ask us to wait, or ``None`` if they don't.

    ``retry-after`` wins, in either form RFC 9110 allows - a delay in seconds
    or an HTTP date - and ``x-ratelimit-reset`` is the fallback, which both
    APIs send as seconds until the window resets.  An unreadable header is
    skipped rather than guessed at, leaving the caller to back off instead.
    """

    raw = headers.get("retry-after")

    if raw is not None:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass

        try:
            reset_at = parsedate_to_datetime(raw)
        except TypeError, ValueError:
            pass
        else:
            return max(0.0, (reset_at - datetime.datetime.now(datetime.UTC)).total_seconds())

    reset = headers.get("x-ratelimit-reset")

    if reset is None:
        return None

    try:
        return max(0.0, float(reset))
    except ValueError:
        return None


def backoff_delay(attempt: int, policy: RetryPolicy) -> float:
    """Return how long to wait before retrying, with *attempt* counting from one.

    The step doubles per attempt up to *policy.max_backoff*, and half of it is
    jittered away, so a retry waits at least half the step but never lines up
    with everything else that was throttled at the same moment.
    """

    step = min(policy.max_backoff, policy.base_wait * 2 ** (attempt - 1))
    half = step / 2

    return half + random.uniform(0, half)


def retry_delay(headers: Mapping[str, str], attempt: int, policy: RetryPolicy) -> tuple[float, float | None]:
    """Return how long to wait before retrying, and what the server asked for.

    The delay is what *headers* asked for when they said anything usable, and
    :func:`backoff_delay` otherwise.  The second element is that server-asked
    value, or ``None`` when there wasn't one - callers that pass it on to their
    own callers need the two kept apart, since a backoff guess is no promise.
    """

    requested = retry_after(headers)

    return (backoff_delay(attempt, policy) if requested is None else requested), requested
