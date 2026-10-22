import datetime
from typing import TYPE_CHECKING, Any

from ..api import APIClient, Errors
from ..cache.exceptions import CacheError
from .exceptions import (
    BloxlinkAPIError,
    BloxlinkError,
    BloxlinkRateLimitError,
    BloxlinkUnavailableError,
)

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("BloxlinkClient",)


class BloxlinkClient(APIClient):
    """Client for the Bloxlink API, available on the bot as ``bot.bloxlink``.

    Resolves and caches the accounts Bloxlink has linked, scoped to a Discord
    guild.  ``None`` means nothing is linked; an API failure raises
    :class:`BloxlinkError`.  Each request spends one of :attr:`DAILY_LIMIT` per
    UTC day, refused with :class:`BloxlinkRateLimitError` once the budget is
    gone.  Requires ``BLOXLINK_API_KEY``.
    """

    name = "Bloxlink"
    errors = Errors(
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

        self.api_key = bot.config.bloxlink_api_key

    def request_headers(self) -> dict[str, str]:
        """Every request carries the API key that pays for it."""

        return {"Authorization": self.api_key}

    def error_detail(self, payload: Any) -> str | None:
        """Bloxlink reports its own errors as ``{"error": "..."}``."""

        error = payload.get("error") if isinstance(payload, dict) else None

        return error or None

    async def before_request(self) -> None:
        """Spend one request from today's quota, raising once it's used up.

        Runs once per attempt, so a request that is rate limited and retried
        spends a second unit.  Raises :class:`BloxlinkRateLimitError` with
        ``retry_after`` set to the seconds left until the UTC-date counter
        resets, arming the fail-fast window so later calls refuse until then;
        a cache failure lets the request through uncounted.
        """

        now = datetime.datetime.now(tz=datetime.UTC)
        key = self.bot.cache.key_from_path("bloxlink", "requests", now.strftime("%Y-%m-%d"))

        midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        resets_in = int((midnight - now).total_seconds())

        try:
            used = await self.bot.cache.incr(key)
        except CacheError as e:
            self.logger.warning(f"Bloxlink quota check failed for {key!r}, sending the request anyway: {e}")
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
                self.logger.warning(f"Bloxlink quota expiry failed for {key!r}, leaving it in Redis: {e}")

        if used > self.DAILY_LIMIT:
            self.arm_rate_limit(resets_in)

            raise BloxlinkRateLimitError(
                f"Bloxlink API quota exhausted: {self.DAILY_LIMIT} requests used for {now.strftime('%Y-%m-%d')} UTC",
                retry_after=resets_in,
            )

        self.logger.debug(f"Bloxlink API quota: {used}/{self.DAILY_LIMIT} used for {now.strftime('%Y-%m-%d')} UTC")

    async def fetch_roblox_id(self, guild_id: int, discord_user_id: int) -> int | None:
        """Fetch the Roblox account linked to *discord_user_id* in *guild_id*."""

        url = f"{self.BASE_URL}/public/guilds/{guild_id}/discord-to-roblox/{discord_user_id}"

        data = await self.request("GET", url)

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

    async def fetch_discord_ids(self, guild_id: int, roblox_id: int) -> list[int] | None:
        """Fetch the Discord accounts linked to *roblox_id* in *guild_id*."""

        url = f"{self.BASE_URL}/public/guilds/{guild_id}/roblox-to-discord/{roblox_id}"

        data = await self.request("GET", url)

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

    async def resolve_roblox_id(self, guild_id: int, discord_user_id: int) -> int | None:
        """Resolve *discord_user_id* to the Roblox account linked in *guild_id*.

        Returns ``None`` if no account is linked and raises
        :class:`BloxlinkError` if the API can't be reached.
        """

        return await self.lookup(
            "bloxlink",
            "discord",
            guild_id,
            discord_user_id,
            cls=int,
            fetch=self.fetch_roblox_id,
            args=(guild_id, discord_user_id),
        )

    async def resolve_discord_ids(self, guild_id: int, roblox_id: int) -> list[int] | None:
        """Resolve *roblox_id* to the Discord accounts linked in *guild_id*.

        Returns ``None`` if no account is linked and raises
        :class:`BloxlinkError` if the API can't be reached.
        """

        return await self.lookup(
            "bloxlink",
            "roblox",
            guild_id,
            roblox_id,
            cls=list[int],
            fetch=self.fetch_discord_ids,
            args=(guild_id, roblox_id),
        )
