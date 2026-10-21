import datetime

import msgspec

from ..model import APIModel, Model

__all__ = ("RobloxUser", "RobloxUserIngest")


class RobloxUserIngest(APIModel):
    """A Roblox user profile, as the users API spells it."""

    id: str
    username: str = msgspec.field(name="name")
    display_name: str = msgspec.field(name="displayName")
    created_at: str = msgspec.field(name="createTime")


class RobloxUser(Model):
    """A Roblox user profile, as the rest of the bot spells it.

    Built from :class:`RobloxUserIngest`.
    """

    id: int
    username: str
    display_name: str
    created_at: datetime.datetime
