from typing import TYPE_CHECKING

import discord
from discord import utils

from ...core.logging import get_logger
from ...core.roblox import RobloxUser

if TYPE_CHECKING:
    from ...core.bluerobot import CircuitBot

__all__ = (
    "add_field",
    "build_embed",
    "send_log",
)

logger = get_logger("roblox_log")

# The invisible padding webhook.py gives every field name: a left-to-right
# mark and an em space, so the two cards line up like they do there.
FIELD_PREFIX = "\u200e\u2003"


def add_field(
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
    if not headshot:
        headshot = f"https://www.roblox.com/headshot-thumbnail/image?userId={roblox_id}&width=150&height=150&format=png"

    value = [f"> **ID:** `{roblox_id}`"]

    if rblx is not None:
        value += [
            f"> **Username:** {rblx.username}",
            f"> **Created:** {utils.format_dt(rblx.created_at, style='R')}",
        ]

    if previous_roblox_id is not None:
        value.append(f"> **Previous:** `{previous_roblox_id}`")

    embed.add_field(
        name=f"{FIELD_PREFIX}✓ {title}",
        value="\n".join(value),
        inline=True,
    )
    embed.set_thumbnail(url=headshot)


async def send_log(bot: "CircuitBot", guild: discord.Guild, embed: discord.Embed) -> None:
    """Post the link-log *embed* to the Roblox link-log channel, never raising.

    The link the log describes is already written, so a missing channel or a
    failed send only logs a warning.
    """
    channel_id = bot.config.league.roblox_link_log_channel_id

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


def build_embed(
    title: str,
    member: discord.Member,
    color: discord.Color,
    rblx: RobloxUser | None,
    roblox_id: int,
    headshot: str | None,
    *,
    previous_roblox_id: int | None = None,
    enforcer: discord.Member | None = None,
    field_title: str = "Roblox",
) -> discord.Embed:
    """The link-log embed for *title* about *member*'s account: header, Roblox
    card, and the enforcer footer when one forced it.
    """
    embed = discord.Embed(
        description=f"# {title}\n-# {member.name} • {member.id}\n",
        color=color,
    )
    add_field(embed, rblx, roblox_id, headshot, title=field_title, previous_roblox_id=previous_roblox_id)

    if enforcer is not None:
        embed.set_footer(text=f"Enforced by: {enforcer.name} • {enforcer.id}", icon_url=enforcer.display_avatar.url)

    return embed
