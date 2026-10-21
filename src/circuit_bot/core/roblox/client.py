from typing import TYPE_CHECKING, Any

import msgspec

from ..api import APIClient, Errors
from .exceptions import RobloxAPIError, RobloxError, RobloxRateLimitError, RobloxUnavailableError
from .models import RobloxUser, RobloxUserIngest

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("RobloxClient", "RobloxUser")


class RobloxClient(APIClient):
    """Client for the Roblox web API, available on the bot as ``bot.roblox``.

    Caches every result, including "user doesn't exist": ``None`` means the user
    doesn't exist, where an API failure raises :class:`RobloxError`.  A 429 is
    retried in place while the wait the API reports is short, and raises
    :class:`RobloxRateLimitError` when it isn't.
    """

    NAME = "Roblox"
    ERRORS = Errors(
        error=RobloxError,
        api=RobloxAPIError,
        unavailable=RobloxUnavailableError,
        ratelimit=RobloxRateLimitError,
    )

    BASE_URL = "https://{api}.roblox.com/v{version}"
    OPEN_CLOUD_URL = "https://apis.roblox.com/cloud/v{version}"

    def __init__(self, *, bot: "CircuitBot") -> None:
        super().__init__(bot=bot)

        self.api_key = bot.config.roblox_api_key

    def error_detail(self, payload: Any) -> str | None:
        """Roblox reports what went wrong as ``{"errors": [{"message": ...}]}``."""

        errors = payload.get("errors") if isinstance(payload, dict) else None

        if not errors:
            return None

        first = errors[0]

        return (first.get("message") if isinstance(first, dict) else None) or None

    async def fetch_user(self, user_id: int) -> RobloxUser | None:
        """Fetch *user_id*'s profile from the users API."""

        url = self.OPEN_CLOUD_URL.format(version="2")
        url += f"/users/{user_id}"

        data = await self.request("GET", url, headers={"x-api-key": self.api_key})

        if data is None:
            return None

        if "code" in data:
            return None

        try:
            ingested = msgspec.convert(data, type=RobloxUserIngest)
        except msgspec.DecodeError as e:
            raise RobloxAPIError(f"Roblox API returned invalid user data for {url} (user id {user_id})") from e

        return msgspec.convert(ingested, RobloxUser, from_attributes=True, strict=False)

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
        :class:`RobloxError` if the API can't be reached.
        """

        return await self.lookup(
            "roblox",
            "user",
            user_id,
            cls=RobloxUser,
            fetch=self.fetch_user,
            args=(user_id,),
        )

    async def get_headshot(self, user_id: int) -> str | None:
        """Get a user's headshot URL.

        Returns ``None`` if the user doesn't exist and raises
        :class:`RobloxError` if the API can't be reached.
        """

        return await self.lookup(
            "roblox",
            "headshot",
            user_id,
            cls=str,
            fetch=self.fetch_headshot,
            args=(user_id,),
        )
