import discord

from ...core.logging import get_logger
from ...core.roblox import RobloxUser

__all__ = (
    "reset_roblox_nickname",
    "roblox_nickname",
    "set_roblox_nickname",
)

logger = get_logger("roblox_nickname")


def roblox_nickname(rblx: RobloxUser) -> str:
    """The nickname Bloxlink gives verified members: "<display name> (@<username>) ✓".

    The parenthetical is dropped when the display name is already the username,
    leaving "<username> ✓".  Discord caps nicknames at 32 characters, so the
    display name is trimmed to keep the username and check mark intact.
    """
    if rblx.display_name == rblx.username:
        return f"{rblx.username} ✓"

    suffix = f" (@{rblx.username}) ✓"
    limit = 32 - len(suffix)
    return f"{rblx.display_name[:limit]}{suffix}"


async def set_roblox_nickname(member: discord.Member, rblx: RobloxUser) -> None:
    """Set *member*'s nickname to Bloxlink's format, never raising."""
    try:
        await member.edit(nick=roblox_nickname(rblx))
    except discord.HTTPException as e:
        logger.warning("Could not set nickname for %d: %s", member.id, e)


async def reset_roblox_nickname(member: discord.Member) -> None:
    """Reset *member*'s nickname to their username, never raising."""
    try:
        await member.edit(nick=None)
    except discord.HTTPException as e:
        logger.warning("Could not reset nickname for %d: %s", member.id, e)
