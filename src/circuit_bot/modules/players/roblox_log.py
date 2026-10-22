from typing import TYPE_CHECKING

import discord
from discord import ui, utils

from ...core.bluerobot.layout import LayoutBuilder, LayoutMixin
from ...core.logging import get_logger
from ...core.roblox import RobloxError, RobloxUser

if TYPE_CHECKING:
    from ...core.bluerobot import CircuitBot
    from .profile import PlayerProfile

__all__ = (
    "add_discord_section",
    "add_roblox_section",
    "discord_member_field",
    "headshot_or_none",
    "roblox_field",
    "roblox_headshot_url",
    "send_roblox_log",
)

logger = get_logger("roblox_log")


def discord_member_field(member: discord.Member) -> str:
    """The section body for a member's card - the id lives in the section heading."""
    return (
        f"\n`Username:` **{member.name}**\n"
        f"`Mention:` {member.mention}\n"
        f"`Joined:` {utils.format_dt(member.joined_at, style='R') if member.joined_at else '**Guest**'}\n"
        f"`Created:` {utils.format_dt(member.created_at, style='R')}"
    )


def roblox_field(rblx: RobloxUser) -> str:
    """The section body for a Roblox card - the id lives in the section heading."""
    return (
        f"\n`Username:` **{rblx.username}**\n"
        f"`Display Name:` **{rblx.display_name}**\n"
        f"`Created:` {utils.format_dt(rblx.created_at, style='R')}"
    )


def roblox_headshot_url(user_id: int) -> str:
    """A headshot URL that resolves without an API call, for when no headshot was fetched."""
    return f"https://www.roblox.com/headshot-thumbnail/image?userId={user_id}&width=150&height=150&format=png"


def add_discord_section(layout: LayoutMixin, member: discord.Member, *, title: str = "Discord") -> None:
    """Append *member*'s card to the link log *layout*: heading, details, avatar."""
    layout.section().text(f"### {title} - `{member.id}`").text(discord_member_field(member)).accessory(
        ui.Thumbnail(member.display_avatar.url)
    ).done()


def add_roblox_section(
    layout: LayoutMixin,
    rblx: RobloxUser | None,
    roblox_id: int,
    headshot: str | None,
    *,
    title: str = "Roblox",
) -> None:
    """Append the Roblox card to the link log *layout*.

    *rblx* is the account's profile when one could be fetched; without it the
    card shows just the id heading.  *headshot* falls back to a plain headshot
    URL so the card always has a thumbnail.
    """
    section = layout.section().text(f"### {title} - `{roblox_id}`")
    if rblx is not None:
        section.text(roblox_field(rblx))
    section.accessory(ui.Thumbnail(headshot or roblox_headshot_url(roblox_id))).done()


async def send_roblox_log(bot: "CircuitBot", guild: discord.Guild, layout: LayoutBuilder) -> None:
    """Post the link-log *layout* to the Roblox link-log channel, never raising.

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
        await channel.send(view=layout)
    except discord.HTTPException as e:
        logger.warning("Could not send Roblox link log: %s", e)


async def headshot_or_none(profile: "PlayerProfile", roblox_id: int) -> str | None:
    """The player's headshot, or ``None`` when it can't be fetched."""
    try:
        return await profile.headshot_url()
    except RobloxError as e:
        logger.warning("Could not fetch Roblox headshot for %d: %s", roblox_id, e)
        return None
