"""The transport the bot's API clients share."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

import aiohttp

from ..cache.exceptions import CacheError
from ..logging import get_logger
from ..ratelimit import OnRatelimit, RetryPolicy, retry_delay
from ..singleflight import SingleFlight
from .exceptions import APIError, APIRateLimitError, APIResponseError, APIUnavailableError

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("APIClient", "Errors")


class Errors(NamedTuple):
    """The errors a client raises, declared together so none is forgotten.

    One per way a request can end badly: the request never got through, the API
    answered with something unusable, the API answered with a server error, and
    the request was refused for asking too often.  Each is the service's own
    subclass, so a caller can catch the one it means.
    """

    error: type[APIError]
    api: type[APIResponseError]
    unavailable: type[APIUnavailableError]
    ratelimit: type[APIRateLimitError]


class APIClient:
    """Base for the clients that talk to a third-party HTTP API.

    Roblox and Bloxlink are the same shape of thing: a session, a retry policy,
    a status code that means "nothing there", a cache in front.  What differs
    is their auth, their endpoints and the errors they raise, so this holds the
    first group once and a subclass declares the second::

        class RobloxClient(APIClient):
            NAME = "Roblox"
            ERRORS = Errors(error=RobloxError, api=RobloxAPIError, ...)

            async def fetch_user(self, user_id: int) -> RobloxUser | None: ...

    A subclass that forgets either is refused when it's defined, rather than
    answering ``None`` from somewhere under a request.  Beyond those two, the
    hooks below are what it overrides when it has something to add to a
    request - each has a default that does nothing, so a client implements the
    ones it has an answer for and no others, and none of them is required
    enough to be abstract.  Its own surface - the ``get_user`` or
    ``resolve_roblox_id`` a command calls - stays on itself, where the prose
    that names it lives.

    The two shared methods are :meth:`request`, which sends one HTTP request and
    turns the ways it can fail into the client's own errors, and :meth:`lookup`,
    which puts the cache in front of a fetch.  Neither is reached through the
    bot: what a caller gets from ``bot.roblox`` is the subclass's own methods.
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
        """Headers every request to this API carries.

        A client that has to authenticate overrides this.  Called once per
        :meth:`request` rather than once per attempt, so a retry sends what the
        first try sent, and whatever a caller passes in its own ``headers`` is
        merged over the top.
        """

        return {}

    async def before_request(self) -> None:
        """Run once per attempt, just before the request goes out.

        Where a client spends something to make a request - a unit of quota, a
        token refresh - it happens here, so only a request that is actually
        about to be sent pays for it.
        """

    def error_detail(self, payload: Any) -> str | None:
        """The reason an error body gives, in the spelling this API uses.

        ``None`` when the body says nothing, which is this default: an API that
        reports its errors some other way overrides it.
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

    async def request(
        self,
        method: str,
        url: str,
        *,
        on_ratelimit: OnRatelimit | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Send an HTTP request to this API and parse the JSON response.

        A 429 is waited out and retried in place while the wait is short enough
        to sit on, up to :attr:`RETRY_POLICY` attempts; *on_ratelimit* is told
        the delay first, so a caller can keep the user posted.  A longer wait
        raises this client's rate limit error instead, carrying ``retry_after``
        only when the API said how long to wait, leaving the caller to come
        back later.

        Returns ``None`` when the API answers 404 - nothing there is an answer,
        not a failure - and otherwise raises the errors the client declared in
        :attr:`ERRORS`: its own error when the request never got through, its
        response error for any other unexpected status or unusable JSON, and
        its unavailable error when the API answers 5xx, which is the API being
        reachable while its backend isn't.
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

                        if on_ratelimit is not None:
                            await on_ratelimit(delay)

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
        on_ratelimit: OnRatelimit | None = None,
    ) -> T | None:
        """Serve *path* from the cache, falling back to *fetch*.

        A ``None`` result is remembered too, as a marker written under the same
        key a value would take - so a lookup of something that isn't there, an
        account nobody linked or a user who doesn't exist, doesn't reach the API
        a second time either, and reading that back costs the one round trip a
        cached value costs.

        Cache failures are logged and skipped: *fetch* is always the source of
        truth.  *on_ratelimit* is handed to *fetch*, which passes it along to
        the request it makes, so only a lookup that ends up waiting at the API
        can report a wait.

        Two callers asking for the same thing at the same moment share the one
        call to *fetch* - the cache covers what happens between answers, this
        covers the window before the first one exists.  What it shares is also
        the first caller's arguments, so *on_ratelimit* belongs to whoever
        started the fetch: a lookup that joins one already in flight isn't told
        about a wait it didn't start.
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

            fetched = await fetch(*args, on_ratelimit=on_ratelimit)

            try:
                if fetched is None:
                    await self.bot.cache.set_absent(*path)
                else:
                    await self.bot.cache.set(*path, value=fetched)
            except CacheError as e:
                self._logger.warning("Cache write failed for %r: %s", key, e)

            return fetched

        return await self._in_flight.run(key, fetch_and_remember)
