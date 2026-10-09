from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import aiohttp

from ..cache.exceptions import CacheError
from ..env import get_env
from ..logging import get_logger
from ..missing import MISSING
from .exceptions import BloxlinkAPIError, BloxlinkError

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

    Every request is authenticated with the ``BLOXLINK_API_KEY`` environment
    variable, so a key from https://blox.link/developers must be set.
    """

    BASE_URL = "https://api.blox.link/v4"

    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot
        self.session = bot.session
        self.api_key = get_env("BLOXLINK_API_KEY")

    async def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any] | None:
        """Send an authenticated HTTP request and parse the JSON response.

        Returns ``None`` when the API answers 404 (nothing linked), raises
        :class:`BloxlinkError` when the request itself fails, and
        :class:`BloxlinkAPIError` when the response has an unexpected status or
        isn't usable JSON.
        """

        logger.debug("Bloxlink API request: %s %s", method, url)

        headers = {"Authorization": self.api_key}
        headers.update(kwargs.pop("headers", {}))

        try:
            async with self.session.request(method, url, headers=headers, **kwargs) as resp:
                if resp.status == 404:
                    return None

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
                    raise BloxlinkAPIError(f"Bloxlink API returned non-JSON ({resp.content_type}) for {method} {url}")

                try:
                    return await resp.json()
                except ValueError as e:
                    raise BloxlinkAPIError(f"Bloxlink API returned invalid JSON for {method} {url}") from e

        except (aiohttp.ClientError, TimeoutError) as e:
            raise BloxlinkError(f"Bloxlink API request failed for {method} {url}") from e

    async def lookup[T: Any](
        self,
        *path: Any,
        cls: type[T],
        fetch: Callable[..., Coroutine[None, None, T | None]],
        args: tuple[Any, ...] = MISSING,
    ) -> T | None:
        """Serve *path* from the cache, falling back to *fetch*.

        A ``None`` result is cached under ``(*path, "missing")`` so repeated
        lookups of unlinked accounts don't hit the API either.  Cache failures
        are logged and skipped - *fetch* is always the source of truth.
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
        value = await fetch(*args)

        try:
            if value is None:
                await self.bot.cache.set(*missing_path, value=True)
            else:
                await self.bot.cache.set(*path, value=value)
        except CacheError as e:
            key = self.bot.cache.key_from_path(*path)
            logger.warning("Cache write failed for %r: %s", key, e)

        return value

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

        Returns ``None`` if no Roblox account is linked and raises
        :class:`BloxlinkError` if the API can't be reached::

            roblox_id = await bot.bloxlink.resolve_roblox_id(guild_id, discord_user_id)
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

        Returns ``None`` if no Discord account is linked and raises
        :class:`BloxlinkError` if the API can't be reached::

            discord_ids = await bot.bloxlink.resolve_discord_ids(guild_id, roblox_id)
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
