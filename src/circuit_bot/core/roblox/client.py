from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import aiohttp
import msgspec

from ..cache.exceptions import CacheError
from ..logging import get_logger
from ..missing import MISSING
from .exceptions import RobloxAPIError, RobloxError
from .models import RobloxUser

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("RobloxClient", "RobloxUser")

logger = get_logger("roblox")


class RobloxClient:
    """Client for the Roblox web API, available on the bot as ``bot.roblox``.

    Every result is cached through ``bot.cache`` for a few minutes, including
    "user doesn't exist", so repeated lookups don't hit the API at all.  A
    lookup returns ``None`` only for a nonexistent user; an API failure raises
    :class:`RobloxError` instead, so the two are never confused.  Cache
    failures are logged and skipped - the API is always the source of truth.
    """

    BASE_URL = "https://{api}.roblox.com/v{version}"

    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot
        self.session = bot.session

    async def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any] | None:
        """Send an HTTP request and parse the JSON response.

        Returns ``None`` when the API answers 404, raises :class:`RobloxError`
        when the request itself fails, and :class:`RobloxAPIError` when the
        response has an unexpected status or isn't usable JSON.
        """

        logger.debug("Roblox API request: %s %s", method, url)

        try:
            async with self.session.request(method, url, **kwargs) as resp:
                if resp.status == 404:
                    return None

                if resp.status != 200:
                    raise RobloxAPIError(f"Roblox API returned {resp.status} for {method} {url}")

                if not resp.content_type.startswith("application/json"):
                    raise RobloxAPIError(f"Roblox API returned non-JSON ({resp.content_type}) for {method} {url}")

                try:
                    return await resp.json()
                except ValueError as e:
                    raise RobloxAPIError(f"Roblox API returned invalid JSON for {method} {url}") from e

        except (aiohttp.ClientError, TimeoutError) as e:
            raise RobloxError(f"Roblox API request failed for {method} {url}") from e

    async def lookup[T: Any](
        self,
        *path: Any,
        cls: type[T],
        fetch: Callable[..., Coroutine[None, None, T | None]],
        args: tuple[Any, ...] = MISSING,
    ) -> T | None:
        """Serve *path* from the cache, falling back to *fetch*.

        A ``None`` result is cached under ``(*path, "missing")`` so repeated
        lookups of nonexistent users don't hit the API either.  Cache failures
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

    async def fetch_user(self, user_id: int) -> RobloxUser | None:
        """Fetch *user_id*'s profile from the users API."""

        url = self.BASE_URL.format(api="users", version="1")
        url += "/users"

        data = await self.request(
            "POST",
            url,
            json={"userIds": [user_id], "excludeBannedUsers": True},
        )

        if data is None:
            return None

        users: list[Any] = data.get("data", [])

        if not users:
            return None

        raw = users[0]
        username = raw.get("name")
        display_name = raw.get("displayName")

        if not username or not display_name:
            raise RobloxAPIError(f"Roblox API returned incomplete user data for {url} (user id {user_id})")

        return msgspec.convert(
            {
                "id": raw.get("id", user_id),
                "username": username,
                "display_name": display_name,
            },
            type=RobloxUser,
        )

    async def fetch_headshot(self, user_id: int) -> str | None:
        """Fetch *user_id*'s headshot URL from the thumbnails API."""

        url = self.BASE_URL.format(api="thumbnails", version="1")
        url += "/users/avatar-headshot"

        params = {
            "userIds": user_id,
            "includeBackground": "false",
            "size": "420x420",
            "format": "Png",
            "isCircular": "false",
        }

        data = await self.request("GET", url, params=params)

        if data is None:
            return None

        users: list[Any] = data.get("data", [])

        if not users:
            return None

        image_url = users[0].get("imageUrl")

        if not image_url:
            raise RobloxAPIError(f"Roblox API returned incomplete headshot data for {url} (user id {user_id})")

        return image_url

    async def get_user(self, user_id: int) -> RobloxUser | None:
        """Get a user's profile.

        Returns ``None`` if the user doesn't exist and raises
        :class:`RobloxError` if the API can't be reached::

            user = await bot.roblox.get_user(1234)
        """

        return await self.lookup("roblox", "user", user_id, cls=RobloxUser, fetch=self.fetch_user, args=(user_id,))

    async def get_headshot(self, user_id: int) -> str | None:
        """Get a user's headshot URL.

        Returns ``None`` if the user doesn't exist and raises
        :class:`RobloxError` if the API can't be reached::

            headshot = await bot.roblox.get_headshot(1234)
        """

        return await self.lookup("roblox", "headshot", user_id, cls=str, fetch=self.fetch_headshot, args=(user_id,))
