from typing import TYPE_CHECKING

import discord
from discord import utils

from ...core.logging import get_logger
from ...core.roblox import RobloxError, RobloxUser

if TYPE_CHECKING:
    from ...core.bluerobot import CircuitBot
    from .profile import PlayerProfile

__all__ = (
    "FIELD_PREFIX",
    "add_discord_field",
    "add_roblox_field",
    "discord_card",
    "headshot_or_none",
    "roblox_card",
    "roblox_headshot_url",
    "send_roblox_log",
    "set_member_footer",
)

logger = get_logger("roblox_log")

# The invisible padding webhook.py gives every field name: a left-to-right
# mark and an em space, so the two cards line up like they do there.
FIELD_PREFIX = "‎ "  # noqa: RUF001


def discord_card(member: discord.Member) -> str:
    """The value for a member's card field - the `>` quote rows from webhook.py."""
    return (
        f"> **ID:** `{member.id}`\n"
        f"> **Mention:** {member.mention}\n"
        f"> **Created:** {utils.format_dt(member.created_at, style='R')}"
    )


def roblox_card(
    rblx: RobloxUser | None,
    roblox_id: int,
    *,
    previous_roblox_id: int | None = None,
) -> str:
    """The value for a Roblox card field - the `>` quote rows from webhook.py.

    Without *rblx* the card shows just the id.  *previous_roblox_id* adds the
    row the self-link and force-link logs show.
    """
    rows = [f"> **ID:** `{roblox_id}`"]

    if rblx is not None:
        rows += [
            f"> **Username:** {rblx.username}",
            f"> **Created:** {utils.format_dt(rblx.created_at, style='R')}",
        ]

    if previous_roblox_id is not None:
        rows.append(f"> **Previous:** `{previous_roblox_id}`")

    return "\n".join(rows)


def roblox_headshot_url(user_id: int) -> str:
    """A headshot URL that resolves without an API call, for when no headshot was fetched."""
    return f"https://www.roblox.com/headshot-thumbnail/image?userId={user_id}&width=150&height=150&format=png"


def add_discord_field(embed: discord.Embed, member: discord.Member, *, title: str = "Discord") -> None:
    """Append *member*'s card to the link log *embed* as an inline field."""
    embed.add_field(name=f"{FIELD_PREFIX}✓ {title}", value=discord_card(member), inline=True)


def add_roblox_field(
    embed: discord.Embed,
    rblx: RobloxUser | None,
    roblox_id: int,
    headshot: str | None,
    *,
    title: str = "Roblox",
    previous_roblox_id: int | None = None,
) -> None:
    """Append the Roblox card to the link log *embed*.

    *rblx* is the account's profile when one could be fetched; without it the
    card shows just the id.  *headshot* falls back to a plain headshot URL so
    the embed always has a thumbnail.
    """
    embed.add_field(
        name=f"{FIELD_PREFIX}✓ {title}",
        value=roblox_card(rblx, roblox_id, previous_roblox_id=previous_roblox_id),
        inline=True,
    )
    embed.set_thumbnail(url=headshot or roblox_headshot_url(roblox_id))


def set_member_footer(embed: discord.Embed, member: discord.Member) -> None:
    """Stamp *member*'s name, id, and avatar on the embed footer, like webhook.py."""
    embed.set_footer(text=f"{member.name} • {member.id}", icon_url=member.display_avatar.with_size(80))


async def send_roblox_log(bot: "CircuitBot", guild: discord.Guild, embed: discord.Embed) -> None:
    """Post the link-log *embed* to the Roblox link-log channel, never raising.

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
        logger.warning("Could not send Roblox link log: %s", e)


async def headshot_or_none(profile: "PlayerProfile", roblox_id: int) -> str | None:
    """The player's headshot, or ``None`` when it can't be fetched."""
    try:
        return await profile.headshot_url()
    except RobloxError as e:
        logger.warning("Could not fetch Roblox headshot for %d: %s", roblox_id, e)
        return None
