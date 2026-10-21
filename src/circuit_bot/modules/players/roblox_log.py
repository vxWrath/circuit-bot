from typing import TYPE_CHECKING

import discord

from ...core.logging import get_logger
from ...core.roblox import RobloxError, RobloxUser

if TYPE_CHECKING:
    from ...core.bluerobot import CircuitBot
    from .profile import PlayerProfile

__all__ = ("discord_member_field", "headshot_or_none", "roblox_field", "send_roblox_log")

logger = get_logger("roblox_log")


def discord_member_field(member: discord.Member) -> str:
    """The embed field for a member, as the Discord side of a link log."""
    return (
        f"`{member.id}`\n"
        f"`Username:` **{member.name}**\n"
        f"`Mention:` {member.mention}\n"
        f"`Joined:` {discord.utils.format_dt(member.joined_at, style='R') if member.joined_at else '**Guest**'}\n"
        f"`Created:` {discord.utils.format_dt(member.created_at, style='R')}"
    )


def roblox_field(rblx: RobloxUser | None, roblox_id: int | None = None) -> str:
    """The embed field for a Roblox account, from its profile when there is one."""
    if rblx is None:
        return f"`{roblox_id}`" if roblox_id is not None else "**None**"

    return (
        f"`{rblx.id}`\n"
        f"`Username:` **{rblx.username}**\n"
        f"`Display Name:` **{rblx.display_name}**\n"
        f"`Created:` {discord.utils.format_dt(rblx.created_at, style='R')}"
    )


async def send_roblox_log(bot: "CircuitBot", guild: discord.Guild, embed: discord.Embed) -> None:
    """Post *embed* to the Roblox link-log channel, never raising.

    The link the log describes is already written, so a missing channel or a
    failed send only logs a warning.
    """
    channel_id = bot.config.roblox_link_log_channel_id

    if not channel_id:
        return

    try:
        channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        logger.warning("Could not fetch Roblox link log channel %d: %s", channel_id, e)
        return

    if channel is None or channel.type != discord.ChannelType.text:
        logger.warning("Roblox link log channel %d is not a text channel", channel_id)
        return

    try:
        await channel.send(embed=embed)
    except discord.HTTPException as e:
        logger.warning("Could not send Roblox link log embed: %s", e)


async def headshot_or_none(profile: "PlayerProfile", roblox_id: int) -> str | None:
    """The player's headshot, or ``None`` when it can't be fetched."""
    try:
        return await profile.headshot_url()
    except RobloxError as e:
        logger.warning("Could not fetch Roblox headshot for %d: %s", roblox_id, e)
        return None
