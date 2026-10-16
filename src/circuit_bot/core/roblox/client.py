from typing import Any

import msgspec

from ..api import APIClient, Errors
from ..ratelimit import OnRatelimit
from .exceptions import RobloxAPIError, RobloxError, RobloxRateLimitError, RobloxUnavailableError
from .models import RobloxUser, RobloxUserIngest

__all__ = ("RobloxClient", "RobloxUser")


class RobloxClient(APIClient):
    """Client for the Roblox web API, available on the bot as ``bot.roblox``.

    Every result is cached through ``bot.cache`` for a few minutes, including
    "user doesn't exist", so repeated lookups don't hit the API at all.  A
    lookup returns ``None`` only for a nonexistent user; an API failure raises
    :class:`RobloxError` instead, so the two are never confused.  Cache
    failures are logged and skipped - the API is always the source of truth.

    Roblox throttles per IP rather than against a published budget, so rather
    than guessing at a ceiling the client reads what the API says about the
    wait: a 429 is retried in place while it's short, and raises
    :class:`RobloxRateLimitError` - carrying ``retry_after`` - when it isn't.
    Sending the request at all is :class:`~circuit_bot.core.api.APIClient`'s;
    what's here is what is Roblox's.
    """

    NAME = "Roblox"
    ERRORS = Errors(
        error=RobloxError,
        api=RobloxAPIError,
        unavailable=RobloxUnavailableError,
        ratelimit=RobloxRateLimitError,
    )

    BASE_URL = "https://{api}.roblox.com/v{version}"

    def error_detail(self, payload: Any) -> str | None:
        """Roblox reports what went wrong as ``{"errors": [{"message": ...}]}``."""

        errors = payload.get("errors") if isinstance(payload, dict) else None

        if not errors:
            return None

        first = errors[0]

        return (first.get("message") if isinstance(first, dict) else None) or None

    async def fetch_user(self, user_id: int, *, on_ratelimit: OnRatelimit | None = None) -> RobloxUser | None:
        """Fetch *user_id*'s profile from the users API."""

        url = self.BASE_URL.format(api="users", version="1")
        url += "/users"

        data = await self.request(
            "POST",
            url,
            json={"userIds": [user_id], "excludeBannedUsers": True},
            on_ratelimit=on_ratelimit,
        )

        if data is None:
            return None

        users: list[Any] = data.get("data", [])

        if not users:
            return None

        try:
            ingested = msgspec.convert(users[0], type=RobloxUserIngest)
        except msgspec.DecodeError as e:
            raise RobloxAPIError(f"Roblox API returned invalid user data for {url} (user id {user_id})") from e

        return msgspec.convert(ingested, RobloxUser, from_attributes=True)

    async def fetch_headshot(self, user_id: int, *, on_ratelimit: OnRatelimit | None = None) -> str | None:
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

        data = await self.request("GET", url, params=params, on_ratelimit=on_ratelimit)

        if data is None:
            return None

        users: list[Any] = data.get("data", [])

        if not users:
            return None

        image_url = users[0].get("imageUrl")

        if not image_url:
            raise RobloxAPIError(f"Roblox API returned incomplete headshot data for {url} (user id {user_id})")

        return image_url

    async def get_user(self, user_id: int, *, on_ratelimit: OnRatelimit | None = None) -> RobloxUser | None:
        """Get a user's profile.

        Returns ``None`` if the user doesn't exist and raises
        :class:`RobloxError` if the API can't be reached.  A cached profile is
        served without touching the API, so *on_ratelimit* only fires on a
        lookup that ends up waiting::

            user = await bot.roblox.get_user(1234, on_ratelimit=show_wait)
        """

        return await self.lookup(
            "roblox",
            "user",
            user_id,
            cls=RobloxUser,
            fetch=self.fetch_user,
            args=(user_id,),
            on_ratelimit=on_ratelimit,
        )

    async def get_headshot(self, user_id: int, *, on_ratelimit: OnRatelimit | None = None) -> str | None:
        """Get a user's headshot URL.

        Returns ``None`` if the user doesn't exist and raises
        :class:`RobloxError` if the API can't be reached.  A cached headshot is
        served without touching the API, so *on_ratelimit* only fires on a
        lookup that ends up waiting::

            headshot = await bot.roblox.get_headshot(1234, on_ratelimit=show_wait)
        """

        return await self.lookup(
            "roblox",
            "headshot",
            user_id,
            cls=str,
            fetch=self.fetch_headshot,
            args=(user_id,),
            on_ratelimit=on_ratelimit,
        )
