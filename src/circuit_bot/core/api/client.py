"""The transport the bot's API clients share."""

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

import aiohttp

from ..cache.exceptions import CacheError
from ..logging import get_logger
from ..ratelimit import RetryPolicy, retry_delay
from ..singleflight import SingleFlight
from .exceptions import (
    APIError,
    APIRateLimitError,
    APIResponseError,
    APIUnavailableError,
)

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("APIClient", "Errors")


class Errors(NamedTuple):
    """The errors a client raises - one per way a request can end badly."""

    error: type[APIError]
    api: type[APIResponseError]
    unavailable: type[APIUnavailableError]
    ratelimit: type[APIRateLimitError]


class APIClient:
    """Base for the clients that talk to a third-party HTTP API.

    A subclass declares :attr:`name` and :attr:`errors`, then builds its own
    methods on :meth:`request` and :meth:`lookup`; the hooks default to doing
    nothing, so a client overrides only the ones it has an answer for.
    """

    name: ClassVar[str]
    errors: ClassVar[Errors]
    retry_policy: ClassVar[RetryPolicy] = RetryPolicy()
    unavailable_window: ClassVar[float] = 30.0  # fail-fast window after a 5xx

    def __init__(self, *, bot: "CircuitBot") -> None:
        self.bot = bot
        self.session = bot.session
        self.logger = get_logger(self.name.casefold())

        # Concurrent lookups of the same key share the fetch that fills it.
        self.in_flight = SingleFlight()

        # Fail-fast windows armed by failures; the rate limit waits out the
        # API's own retry_after, the outage window is a conservative guess.
        self.rate_limited_until = 0.0
        self.unavailable_until = 0.0

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        missing = [attr for attr in ("name", "errors") if not getattr(cls, attr, None)]
        if missing:
            raise TypeError(f"{cls.__name__} must declare {', '.join(missing)}")

    # -- hooks ---------------------------------------------------------------

    def request_headers(self) -> dict[str, str]:
        """Headers every request carries - override to authenticate."""

        return {}

    async def before_request(self) -> None:
        """Run once per attempt, just before the request goes out.

        Where a client spends something to make a request - a unit of quota -
        it happens here, so only an attempt actually being made pays for it.
        """

    def error_detail(self, payload: Any) -> str | None:
        """The reason an error body gives, in this API's spelling."""

        return None

    def arm_rate_limit(self, retry_after: float) -> None:
        """Remember a rate limit so later calls fail fast until it passes."""

        self.rate_limited_until = time.time() + retry_after

    # -- requests ------------------------------------------------------------

    async def error_message(self, message: str, response: aiohttp.ClientResponse) -> str:
        """*message*, with the reason an error body gives appended."""

        try:
            payload = await response.json()
        except aiohttp.ClientError, ValueError:
            return message

        detail = self.error_detail(payload)
        return f"{message}: {detail}" if detail else message

    def check_fail_fast(self, method: str, url: str) -> None:
        """Raise the remembered failure instead of sending, while it's still fresh."""

        now = time.time()

        if now < self.rate_limited_until:
            remaining = self.rate_limited_until - now
            self.logger.debug(f"{self.name} API rate limited, failing fast for {remaining:.0f}s: {method} {url}")
            raise self.errors.ratelimit(
                f"{self.name} API rate limited; failing fast for {remaining:.0f}s on {method} {url}",
                retry_after=remaining,
            )

        if now < self.unavailable_until:
            remaining = self.unavailable_until - now
            self.logger.debug(f"{self.name} API unavailable, failing fast for {remaining:.0f}s: {method} {url}")
            raise self.errors.unavailable(
                f"{self.name} API unavailable; failing fast for {remaining:.0f}s on {method} {url}"
            )

    async def handle_response(self, response: aiohttp.ClientResponse, method: str, url: str) -> dict[str, Any] | None:
        """Parse a non-429 response, raising the error its status means."""

        if response.status == 404:
            return None

        if response.status >= 500:
            self.unavailable_until = time.time() + self.unavailable_window
            raise self.errors.unavailable(f"{self.name} API returned {response.status} for {method} {url}")

        if response.status != 200:
            message = f"{self.name} API returned {response.status} for {method} {url}"
            raise self.errors.api(await self.error_message(message, response))

        if not response.content_type.startswith("application/json"):
            raise self.errors.api(f"{self.name} API returned non-JSON ({response.content_type}) for {method} {url}")

        try:
            return await response.json()
        except ValueError as e:
            raise self.errors.api(f"{self.name} API returned invalid JSON for {method} {url}") from e

    async def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any] | None:
        """Send one HTTP request and parse the JSON response.

        A short 429 wait is retried in place up to :attr:`retry_policy`; a
        longer one or a 5xx arms a fail-fast window that refuses later calls.
        Returns ``None`` for a 404 and raises the matching error from
        :attr:`errors` for anything else.
        """

        self.check_fail_fast(method, url)
        self.logger.debug(f"{self.name} API request: {method} {url}")

        # Built once, so a retry resends what the first try sent.
        headers = self.request_headers()
        headers.update(kwargs.pop("headers", {}))

        attempt = 0

        while True:
            attempt += 1

            await self.before_request()

            try:
                async with self.session.request(method, url, headers=headers, **kwargs) as resp:
                    if resp.status == 429:
                        delay, requested = retry_delay(resp.headers, attempt, self.retry_policy)

                        # Hand the connection back before sleeping on it.
                        resp.release()

                        if attempt < self.retry_policy.attempts and delay <= self.retry_policy.max_wait:
                            self.logger.warning(
                                f"{self.name} API rate limited {method} {url}, "
                                f"retrying in {delay:.1f}s (attempt {attempt}/{self.retry_policy.attempts})"
                            )
                            await asyncio.sleep(delay)
                            continue

                        # Only a header the API sent is a promise - our own
                        # backoff is a guess.
                        if requested is not None:
                            self.arm_rate_limit(requested)

                        asked = f" (asked for {requested:.1f}s)" if requested is not None else ""
                        raise self.errors.ratelimit(
                            f"{self.name} API rate limited {method} {url}{asked}", retry_after=requested
                        )

                    return await self.handle_response(resp, method, url)

            except (aiohttp.ClientError, TimeoutError) as e:
                raise self.errors.error(f"{self.name} API request failed for {method} {url}") from e

    # -- reads ---------------------------------------------------------------

    async def lookup[T: Any](
        self,
        *path: Any,
        cls: type[T],
        fetch: Callable[..., Coroutine[None, None, T | None]],
        args: tuple[Any, ...] = (),
    ) -> T | None:
        """Serve *path* from the cache, falling back to *fetch*.

        A ``None`` from *fetch* is remembered as an absent marker; a cache
        failure is logged and skipped.  Concurrent callers asking for the same
        thing share one call to *fetch*.
        """

        key = self.bot.cache.key_from_path(*path)

        try:
            cached, absent = await self.bot.cache.get_or_absent(*path, cls=cls)

            if absent:
                return None

            if cached is not None:
                return cached
        except CacheError as e:
            self.logger.warning(f"Cache read failed for {key!r}, fetching from the API instead: {e}")

        async def fetch_and_remember() -> T | None:
            fetched = await fetch(*args)

            try:
                if fetched is None:
                    await self.bot.cache.set_absent(*path)
                else:
                    await self.bot.cache.set(*path, value=fetched)
            except CacheError as e:
                self.logger.warning(f"Cache write failed for {key!r}: {e}")

            return fetched

        return await self.in_flight.run(key, fetch_and_remember)
