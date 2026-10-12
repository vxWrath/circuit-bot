import asyncio
import datetime
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import aiohttp

from ..cache.exceptions import CacheError
from ..env import get_env
from ..logging import get_logger
from ..missing import MISSING
from ..ratelimit import OnRatelimit, RetryPolicy, retry_delay
from .exceptions import (
    BloxlinkAPIError,
    BloxlinkError,
    BloxlinkRateLimitError,
    BloxlinkUnavailableError,
)

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("BloxlinkClient",)

logger = get_logger("bloxlink")


class BloxlinkClient:
    """Client for the Bloxlink API, available on the bot as ``bot.bloxlink``.

    Resolves the accounts Bloxlink has linked, scoped to a Discord guild.
    Every result is cached through ``bot.cache`` for a few minutes, including
    "not linked", so repeated lookups don't hit the API at all.  A lookup
    returns ``None`` only when no account is linked; an API failure raises
    :class:`BloxlinkError` instead, so the two are never confused.  Cache
    failures are logged and skipped - the API is always the source of truth.

    Bloxlink allows 2000 requests per day per key, so every request first
    spends one from a Redis counter keyed by the UTC date, and once the day's
    budget is gone requests are refused with :class:`BloxlinkRateLimitError`
    rather than sent - see :meth:`validate_request`.  Requests are throttled
    minute to minute as well, so a 429 is waited out and retried while the wait
    is short enough, and raises :class:`BloxlinkRateLimitError` - the same
    error, carrying ``retry_after`` - when it isn't.

    Every request is authenticated with the ``BLOXLINK_API_KEY`` environment
    variable, so a key from https://blox.link/developers must be set.
    """

    BASE_URL = "https://api.blox.link/v4"
    RETRY_POLICY = RetryPolicy()

    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot
        self.session = bot.session
        self.api_key = get_env("BLOXLINK_API_KEY")

    async def request(
        self,
        method: str,
        url: str,
        *,
        on_ratelimit: OnRatelimit | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Send an authenticated HTTP request and parse the JSON response.

        The request spends one from today's quota per attempt, so a spent quota
        raises :class:`BloxlinkRateLimitError` before anything goes out.  A 429
        is waited out and retried in place while the wait is short enough to
        sit on, up to :attr:`RETRY_POLICY` attempts; *on_ratelimit* is told the
        delay first, so a caller can keep the user posted.  A longer wait
        raises :class:`BloxlinkRateLimitError` instead, carrying ``retry_after``
        only when Bloxlink said how long to wait - which it isn't documented to
        do - leaving the caller to come back later.

        Returns ``None`` when the API answers 404 (nothing linked), raises
        :class:`BloxlinkError` when the request itself fails,
        :class:`BloxlinkUnavailableError` when Bloxlink answers 5xx -
        reachable, but its backend isn't - and :class:`BloxlinkAPIError` when
        the response has an unexpected status or isn't usable JSON.
        """

        logger.debug("Bloxlink API request: %s %s", method, url)

        # Built once: a retry must not lose the caller's headers to the pop.
        headers = {"Authorization": self.api_key}
        headers.update(kwargs.pop("headers", {}))

        attempt = 0

        while True:
            attempt += 1

            await self.validate_request()

            try:
                async with self.session.request(method, url, headers=headers, **kwargs) as resp:
                    if resp.status == 429:
                        delay, requested = retry_delay(resp.headers, attempt, self.RETRY_POLICY)

                        if attempt >= self.RETRY_POLICY.attempts or delay > self.RETRY_POLICY.max_wait:
                            # Only a header Bloxlink actually sent is a promise to
                            # the caller - our own backoff is a guess.
                            asked = f" (asked for {requested:.1f}s)" if requested is not None else ""
                            raise BloxlinkRateLimitError(
                                f"Bloxlink API rate limited {method} {url}{asked}",
                                retry_after=requested,
                            )

                        # Hand the connection back before sleeping on it.
                        resp.release()

                        logger.warning(
                            "Bloxlink API rate limited %s %s, retrying in %.1fs (attempt %d/%d)",
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
                        raise BloxlinkUnavailableError(f"Bloxlink API returned {resp.status} for {method} {url}")

                    if resp.status != 200:
                        message = f"Bloxlink API returned {resp.status} for {method} {url}"

                        # Bloxlink reports its own errors as {"error": "..."} - surface them.
                        try:
                            payload = await resp.json()
                        except aiohttp.ClientError, ValueError:
                            pass
                        else:
                            error = payload.get("error") if isinstance(payload, dict) else None
                            if error:
                                message += f": {error}"

                        raise BloxlinkAPIError(message)

                    if not resp.content_type.startswith("application/json"):
                        raise BloxlinkAPIError(
                            f"Bloxlink API returned non-JSON ({resp.content_type}) for {method} {url}"
                        )

                    try:
                        return await resp.json()
                    except ValueError as e:
                        raise BloxlinkAPIError(f"Bloxlink API returned invalid JSON for {method} {url}") from e

            except (aiohttp.ClientError, TimeoutError) as e:
                raise BloxlinkError(f"Bloxlink API request failed for {method} {url}") from e

    async def validate_request(self) -> None:
        """Spend one request from today's quota, raising once it's used up.

        The counter is a single Redis entry keyed by the UTC date
        (``bloxlink:requests:2026-10-12``), so it starts over on its own at
        midnight UTC and no reset job is needed.  The increment comes first, so
        concurrent requests each get their own number and only the first
        :attr:`DAILY_LIMIT` of them are ever sent; attempts refused by the
        guard are counted too, which makes the counter a tally of demand rather
        than of requests that reached Bloxlink.

        Raises :class:`BloxlinkRateLimitError` once the limit is exceeded, with
        ``retry_after`` set to the seconds left until the counter resets.
        """

        now = datetime.datetime.now(tz=datetime.UTC)
        key = self.bot.cache.key_from_path("bloxlink", "requests", now.strftime("%Y-%m-%d"))

        midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        resets_in = int((midnight - now).total_seconds())

        try:
            used = await self.bot.cache.incr(key)
        except CacheError as e:
            logger.warning("Bloxlink quota check failed for %r, sending the request anyway: %s", key, e)
            return

        if used == 1:
            try:
                # INCR creates the counter without a TTL, and it only says
                # anything about the day it counts, so drop it shortly after
                # that day is over instead of leaving it in Redis for good.
                await self.bot.cache.expire(key, ex=resets_in + 1800)  # 30 minutes grace before expiry
            except CacheError as e:
                # A missed expiry only leaves a stale counter behind, so it's
                # not worth refusing the request over.
                logger.warning("Bloxlink quota expiry failed for %r, leaving it in Redis: %s", key, e)

        if used > 2000:
            raise BloxlinkRateLimitError(
                f"Bloxlink API quota exhausted: 2000 requests used for {now.strftime('%Y-%m-%d')} UTC",
                retry_after=resets_in,
            )

        logger.debug("Bloxlink API quota: %d/%d used for %s UTC", used, 2000, now.strftime("%Y-%m-%d"))

    async def lookup[T: Any](
        self,
        *path: Any,
        cls: type[T],
        fetch: Callable[..., Coroutine[None, None, T | None]],
        args: tuple[Any, ...] = MISSING,
        on_ratelimit: OnRatelimit | None = None,
    ) -> T | None:
        """Serve *path* from the cache, falling back to *fetch*.

        A ``None`` result is cached under ``(*path, "missing")`` so repeated
        lookups of unlinked accounts don't hit the API either.  Cache failures
        are logged and skipped - *fetch* is always the source of truth.
        *on_ratelimit* is handed to *fetch*, which passes it along to the
        request it makes, so only a lookup that actually reaches the API can
        report a wait.
        """

        missing_path = (*path, "missing")

        try:
            cached = await self.bot.cache.get(*path, cls=cls)
            if cached is not None:
                return cached

            if await self.bot.cache.exists(*missing_path):
                return None
        except CacheError as e:
            key = self.bot.cache.key_from_path(*path)
            logger.warning("Cache read failed for %r, fetching from the API instead: %s", key, e)

        args = args if args is not MISSING else ()
        value = await fetch(*args, on_ratelimit=on_ratelimit)

        try:
            if value is None:
                await self.bot.cache.set(*missing_path, value=True)
            else:
                await self.bot.cache.set(*path, value=value)
        except CacheError as e:
            key = self.bot.cache.key_from_path(*path)
            logger.warning("Cache write failed for %r: %s", key, e)

        return value

    async def fetch_roblox_id(
        self,
        guild_id: int,
        discord_user_id: int,
        *,
        on_ratelimit: OnRatelimit | None = None,
    ) -> int | None:
        """Fetch the Roblox account linked to *discord_user_id* in *guild_id*."""

        url = f"{self.BASE_URL}/public/guilds/{guild_id}/discord-to-roblox/{discord_user_id}"

        data = await self.request("GET", url, on_ratelimit=on_ratelimit)

        if data is None:
            return None

        raw = data.get("robloxID")

        if raw is None:
            raise BloxlinkAPIError(
                f"Bloxlink API returned incomplete data for {url} (discord user id {discord_user_id})"
            )

        try:
            return int(raw)
        except (TypeError, ValueError) as e:
            raise BloxlinkAPIError(f"Bloxlink API returned an invalid Roblox ID {raw!r} for {url}") from e

    async def fetch_discord_ids(
        self,
        guild_id: int,
        roblox_id: int,
        *,
        on_ratelimit: OnRatelimit | None = None,
    ) -> list[int] | None:
        """Fetch the Discord accounts linked to *roblox_id* in *guild_id*."""

        url = f"{self.BASE_URL}/public/guilds/{guild_id}/roblox-to-discord/{roblox_id}"

        data = await self.request("GET", url, on_ratelimit=on_ratelimit)

        if data is None:
            return None

        raw = data.get("discordIDs")

        if raw is None:
            raise BloxlinkAPIError(f"Bloxlink API returned incomplete data for {url} (roblox id {roblox_id})")

        ids: list[int] = []

        for item in raw:
            try:
                ids.append(int(item))
            except (TypeError, ValueError) as e:
                raise BloxlinkAPIError(f"Bloxlink API returned an invalid Discord ID {item!r} for {url}") from e

        return ids or None

    async def resolve_roblox_id(
        self,
        guild_id: int,
        discord_user_id: int,
        *,
        on_ratelimit: OnRatelimit | None = None,
    ) -> int | None:
        """Resolve *discord_user_id* to the Roblox account linked in *guild_id*.

        Returns ``None`` if no Roblox account is linked and raises
        :class:`BloxlinkError` if the API can't be reached.  A cached link is
        served without touching the API, so *on_ratelimit* only fires on a
        lookup that ends up waiting::

            roblox_id = await bot.bloxlink.resolve_roblox_id(guild_id, discord_user_id, on_ratelimit=show_wait)
        """

        return await self.lookup(
            "bloxlink",
            "discord",
            guild_id,
            discord_user_id,
            cls=int,
            fetch=self.fetch_roblox_id,
            args=(guild_id, discord_user_id),
            on_ratelimit=on_ratelimit,
        )

    async def resolve_discord_ids(
        self,
        guild_id: int,
        roblox_id: int,
        *,
        on_ratelimit: OnRatelimit | None = None,
    ) -> list[int] | None:
        """Resolve *roblox_id* to the Discord accounts linked in *guild_id*.

        Returns ``None`` if no Discord account is linked and raises
        :class:`BloxlinkError` if the API can't be reached.  A cached link is
        served without touching the API, so *on_ratelimit* only fires on a
        lookup that ends up waiting::

            discord_ids = await bot.bloxlink.resolve_discord_ids(guild_id, roblox_id, on_ratelimit=show_wait)
        """

        return await self.lookup(
            "bloxlink",
            "roblox",
            guild_id,
            roblox_id,
            cls=list[int],
            fetch=self.fetch_discord_ids,
            args=(guild_id, roblox_id),
            on_ratelimit=on_ratelimit,
        )
