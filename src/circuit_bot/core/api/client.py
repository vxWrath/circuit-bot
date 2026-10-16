"""The transport the bot's API clients share."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

import aiohttp

from ..cache.exceptions import CacheError
from ..logging import get_logger
from ..ratelimit import RetryPolicy, retry_delay
from ..singleflight import SingleFlight
from .exceptions import APIError, APIRateLimitError, APIResponseError, APIUnavailableError

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("APIClient", "Errors")


class Errors(NamedTuple):
    """The errors a client raises, declared together so none is forgotten.

    One per way a request can end badly, and each is the service's own subclass,
    so a caller can catch the one it means.
    """

    error: type[APIError]
    api: type[APIResponseError]
    unavailable: type[APIUnavailableError]
    ratelimit: type[APIRateLimitError]


class APIClient:
    """Base for the clients that talk to a third-party HTTP API.

    A subclass must declare :attr:`NAME` and :attr:`ERRORS`, and is refused at
    definition time otherwise.  The hooks below default to doing nothing, so a
    client overrides only the ones it has an answer for.

    :meth:`request` sends one HTTP request and raises the client's own errors;
    :meth:`lookup` puts the cache in front of a fetch.  Both are internal: what
    ``bot.roblox`` exposes is the subclass's own methods.
    """

    NAME: ClassVar[str]
    """The API's name, spelled the way this client's messages should read: ``Roblox``."""

    ERRORS: ClassVar[Errors]
    """The errors this client raises - see :class:`Errors`."""

    RETRY_POLICY: ClassVar[RetryPolicy] = RetryPolicy()
    """How long this client is willing to sit on a rate limit."""

    def __init__(self, *, bot: "CircuitBot") -> None:
        self.bot = bot
        self.session = bot.session

        # Named after the API, because the prose of every message this client
        # logs names it too.
        self._logger = get_logger(self.NAME.casefold())

        # Keyed by the cache key a lookup will be remembered under, so two
        # lookups that would share an entry share the fetch that fills it.
        self._in_flight = SingleFlight()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        missing = [name for name in ("NAME", "ERRORS") if not getattr(cls, name, None)]

        if missing:
            raise TypeError(f"{cls.__name__} must declare {', '.join(missing)}")

    # -- hooks ---------------------------------------------------------------

    def request_headers(self) -> dict[str, str]:
        """Headers every request to this API carries - override to authenticate.

        Built once per :meth:`request`, not per attempt, so a retry resends what
        the first try sent; a caller's own ``headers`` merge over the top.
        """

        return {}

    async def before_request(self) -> None:
        """Run once per attempt, just before the request goes out.

        Where a client spends something to make a request - a unit of quota, a
        token refresh - it happens here, so only an attempt actually being made
        pays for it.
        """

    def error_detail(self, payload: Any) -> str | None:
        """The reason an error body gives, in the spelling this API uses.

        ``None`` by default, for an API that reports its errors some other way.
        """

        return None

    # -- requests ------------------------------------------------------------

    async def error_message(self, message: str, response: aiohttp.ClientResponse) -> str:
        """*message*, with the reason an error body gives appended when it gives one."""

        try:
            payload = await response.json()
        except aiohttp.ClientError, ValueError:
            return message

        detail = self.error_detail(payload)

        return f"{message}: {detail}" if detail else message

    async def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any] | None:
        """Send an HTTP request to this API and parse the JSON response.

        A 429 is waited out and retried in place while the wait is short enough to
        sit on, up to :attr:`RETRY_POLICY` attempts; a longer wait raises the
        client's rate limit error, carrying ``retry_after`` when the API said how
        long.

        Returns ``None`` when the API answers 404.  Any other failure raises what
        the client declared in :attr:`ERRORS`: its own error when the request
        never got through, its response error for an unexpected status or
        unusable JSON, and its unavailable error for a 5xx.
        """

        self._logger.debug("%s API request: %s %s", self.NAME, method, url)

        # Built once: a retry must not lose the caller's headers to the pop.
        headers = self.request_headers()
        headers.update(kwargs.pop("headers", {}))

        attempt = 0

        while True:
            attempt += 1

            await self.before_request()

            try:
                async with self.session.request(method, url, headers=headers, **kwargs) as resp:
                    if resp.status == 429:
                        delay, requested = retry_delay(resp.headers, attempt, self.RETRY_POLICY)

                        if attempt >= self.RETRY_POLICY.attempts or delay > self.RETRY_POLICY.max_wait:
                            # Only a header the API actually sent is a promise
                            # to the caller - our own backoff is a guess.
                            asked = f" (asked for {requested:.1f}s)" if requested is not None else ""
                            raise self.ERRORS.ratelimit(
                                f"{self.NAME} API rate limited {method} {url}{asked}",
                                retry_after=requested,
                            )

                        # Hand the connection back before sleeping on it.
                        resp.release()

                        self._logger.warning(
                            "%s API rate limited %s %s, retrying in %.1fs (attempt %d/%d)",
                            self.NAME,
                            method,
                            url,
                            delay,
                            attempt,
                            self.RETRY_POLICY.attempts,
                        )

                        await asyncio.sleep(delay)
                        continue

                    if resp.status == 404:
                        return None

                    if resp.status >= 500:
                        raise self.ERRORS.unavailable(f"{self.NAME} API returned {resp.status} for {method} {url}")

                    if resp.status != 200:
                        message = f"{self.NAME} API returned {resp.status} for {method} {url}"
                        raise self.ERRORS.api(await self.error_message(message, resp))

                    if not resp.content_type.startswith("application/json"):
                        raise self.ERRORS.api(
                            f"{self.NAME} API returned non-JSON ({resp.content_type}) for {method} {url}"
                        )

                    try:
                        return await resp.json()
                    except ValueError as e:
                        raise self.ERRORS.api(f"{self.NAME} API returned invalid JSON for {method} {url}") from e

            except (aiohttp.ClientError, TimeoutError) as e:
                raise self.ERRORS.error(f"{self.NAME} API request failed for {method} {url}") from e

    # -- reads ---------------------------------------------------------------

    async def lookup[T: Any](
        self,
        *path: Any,
        cls: type[T],
        fetch: Callable[..., Coroutine[None, None, T | None]],
        args: tuple[Any, ...] = (),
    ) -> T | None:
        """Serve *path* from the cache, falling back to *fetch*.

        A ``None`` from *fetch* is remembered too, as an absent marker under the
        same key a value would take.  A cache failure is logged and skipped;
        *fetch* stays the source of truth.

        Callers asking for the same thing at the same moment share one call to
        *fetch*, and with it the first caller's arguments.
        """

        key = self.bot.cache.key_from_path(*path)

        try:
            cached, absent = await self.bot.cache.get_or_absent(*path, cls=cls)

            if absent:
                return None

            if cached is not None:
                return cached
        except CacheError as e:
            self._logger.warning("Cache read failed for %r, fetching from the API instead: %s", key, e)

        async def fetch_and_remember() -> T | None:
            """Fetch, then keep what came back - the unit concurrent callers share."""

            fetched = await fetch(*args)

            try:
                if fetched is None:
                    await self.bot.cache.set_absent(*path)
                else:
                    await self.bot.cache.set(*path, value=fetched)
            except CacheError as e:
                self._logger.warning("Cache write failed for %r: %s", key, e)

            return fetched

        return await self._in_flight.run(key, fetch_and_remember)
