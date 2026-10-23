from typing import Literal

import discord

from .app_commands import CheckFailure, CheckType, check
from .interaction import Interaction

__all__ = (
    "DEV_IDS",
    "MINIMUM_PERMISSIONS",
    "developer_only",
    "guild_owner_only",
    "is_developer",
)

DEV_IDS = [
    1104883688279384156,  # godmadewrath
    450136921327271946,  # wrath
]

MINIMUM_PERMISSIONS = {
    p: status
    for p, status in discord.Permissions(
        view_channel=True, send_messages=True, embed_links=True, read_message_history=True
    )
    if status
}


def is_developer(user: discord.User | discord.Member) -> bool:
    return user.id in DEV_IDS


def developer_only():
    async def pred(interaction: Interaction) -> Literal[True]:
        if is_developer(interaction.user):
            return True
        raise CheckFailure(CheckType.DEVELOPER)

    return check(pred)


def guild_owner_only():
    def pred(interaction: Interaction) -> Literal[True]:
        if not interaction.guild:
            raise CheckFailure(CheckType.GUILD_OWNER)

        if interaction.user.id != interaction.guild.owner_id:
            raise CheckFailure(CheckType.GUILD_OWNER)

        return True

    return check(pred)
