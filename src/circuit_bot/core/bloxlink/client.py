import datetime
from typing import TYPE_CHECKING, Any

from ..api import APIClient, Errors
from ..cache.exceptions import CacheError
from ..env import get_env
from ..ratelimit import OnRatelimit
from .exceptions import BloxlinkAPIError, BloxlinkError, BloxlinkRateLimitError, BloxlinkUnavailableError

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("BloxlinkClient",)


class BloxlinkClient(APIClient):
    """Client for the Bloxlink API, available on the bot as ``bot.bloxlink``.

    Resolves the accounts Bloxlink has linked, scoped to a Discord guild.
    Every result is cached through ``bot.cache`` for a few minutes, including
    "not linked", so repeated lookups don't hit the API at all.  A lookup
    returns ``None`` only when no account is linked; an API failure raises
    :class:`BloxlinkError` instead, so the two are never confused.  Cache
    failures are logged and skipped - the API is always the source of truth.

    Bloxlink allows :attr:`DAILY_LIMIT` requests per day per key, so every
    request first spends one from a Redis counter keyed by the UTC date, and
    once the day's budget is gone requests are refused with
    :class:`BloxlinkRateLimitError` rather than sent - see
    :meth:`before_request`.  Requests are throttled minute to minute as well,
    so a 429 is waited out and retried while the wait is short enough, and
    raises :class:`BloxlinkRateLimitError` - the same error, carrying
    ``retry_after`` - when it isn't.

    Every request is authenticated with the ``BLOXLINK_API_KEY`` environment
    variable, so a key from https://blox.link/developers must be set.

    Sending the request at all is :class:`~circuit_bot.core.api.APIClient`'s;
    what's here is what is Bloxlink's - the key, the quota, and the endpoints.
    """

    NAME = "Bloxlink"
    ERRORS = Errors(
        error=BloxlinkError,
        api=BloxlinkAPIError,
        unavailable=BloxlinkUnavailableError,
        ratelimit=BloxlinkRateLimitError,
    )

    BASE_URL = "https://api.blox.link/v4"

    DAILY_LIMIT = 2000  # requests per day per API key
    QUOTA_GRACE = 1800  # seconds the counter outlives the day it counted

    def __init__(self, *, bot: "CircuitBot") -> None:
        super().__init__(bot=bot)

        self.api_key = get_env("BLOXLINK_API_KEY")

    def request_headers(self) -> dict[str, str]:
        """Every request carries the API key that pays for it."""

        return {"Authorization": self.api_key}

    def error_detail(self, payload: Any) -> str | None:
        """Bloxlink reports its own errors as ``{"error": "..."}``."""

        error = payload.get("error") if isinstance(payload, dict) else None

        return error or None

    async def before_request(self) -> None:
        """Spend one request from today's quota, raising once it's used up.

        The counter is a single Redis entry keyed by the UTC date
        (``bloxlink:requests:2026-10-12``), so it starts over on its own at
        midnight UTC and no reset job is needed.  The increment comes first, so
        concurrent requests each get their own number and only the first
        :attr:`DAILY_LIMIT` of them are ever sent; attempts refused by the
        guard are counted too, which makes the counter a tally of demand rather
        than of requests that reached Bloxlink.

        Runs once per attempt, so a request that is rate limited and retried
        spends a second unit on the retry - which is what Bloxlink counts.

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
            self._logger.warning("Bloxlink quota check failed for %r, sending the request anyway: %s", key, e)
            return

        if used == 1:
            try:
                # INCR creates the counter without a TTL, and it only says
                # anything about the day it counts, so drop it shortly after
                # that day is over instead of leaving it in Redis for good.
                await self.bot.cache.expire(key, ex=resets_in + self.QUOTA_GRACE)
            except CacheError as e:
                # A missed expiry only leaves a stale counter behind, so it's
                # not worth refusing the request over.
                self._logger.warning("Bloxlink quota expiry failed for %r, leaving it in Redis: %s", key, e)

        if used > self.DAILY_LIMIT:
            raise BloxlinkRateLimitError(
                f"Bloxlink API quota exhausted: {self.DAILY_LIMIT} requests used for {now.strftime('%Y-%m-%d')} UTC",
                retry_after=resets_in,
            )

        self._logger.debug(
            "Bloxlink API quota: %d/%d used for %s UTC", used, self.DAILY_LIMIT, now.strftime("%Y-%m-%d")
        )

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
