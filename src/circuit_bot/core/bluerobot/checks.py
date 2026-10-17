from enum import Enum
from typing import Any, Literal

import discord

from .app_command import check
from .interaction import GuildOnly, Interaction

__all__ = (
    "DEV_IDS",
    "developer_only",
    "guild_owner_only",
    "is_developer",
)

DEV_IDS = [
    1104883688279384156,  # godmadewrath
    450136921327271946,  # wrath
]


class CheckFailure(discord.app_commands.CheckFailure):
    def __init__(self, check: "Check", **kwargs: Any):
        self.check = check
        self.kwargs = kwargs


class Check(Enum):
    DEVELOPER = "developer_only"
    GUILD_OWNER = "guild_owner_only"


def is_developer(user: discord.User | discord.Member) -> bool:
    return user.id in DEV_IDS


def developer_only():
    async def pred(interaction: Interaction) -> Literal[True]:
        if is_developer(interaction.user):
            return True
        raise CheckFailure(Check.DEVELOPER)

    return check(pred)


def guild_owner_only():
    def pred(interaction: Interaction) -> Literal[True]:
        if not interaction.guild:
            raise GuildOnly()

        if interaction.user.id != interaction.guild.owner_id:
            raise CheckFailure(Check.GUILD_OWNER)

        return True

    return check(pred)
