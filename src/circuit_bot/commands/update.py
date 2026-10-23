from datetime import timedelta

import discord
from discord.ext import commands

from ..core import (
    BloxlinkError,
    BloxlinkRateLimitError,
    CircuitBot,
    CircuitGroup,
    Color,
    CommonEmojis,
    DeferOptions,
    Interaction,
    InteractionOptions,
    RecordNotFoundError,
    RobloxError,
    RobloxRateLimitError,
    cooldown,
    shared_cooldown,
)
from ..modules import (
    PlayerService,
    add_discord_field,
    add_roblox_field,
    headshot_or_none,
    send_roblox_log,
    set_member_footer,
)

DEFER = {"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))}


class Update(commands.Cog):
    def __init__(self, bot: CircuitBot):
        self.bot = bot

    update = CircuitGroup(name="update", description="Update your own data.", guild_only=True)

    @update.command(name="roblox", description="Update your linked Roblox account.", extras=DEFER)
    @shared_cooldown("bloxlink", limit=1, duration=timedelta(minutes=1))
    @shared_cooldown("roblox", limit=1, duration=timedelta(minutes=1))
    @cooldown("weekly", limit=1, duration=timedelta(days=7))
    async def update_roblox(self, interaction: Interaction):
        member = interaction.require_member()
        service = self.bot.get_service(PlayerService)
        cooldowns = interaction.cooldowns

        # Resolve through Bloxlink, like the join event does.
        try:
            roblox_id = await self.bot.bloxlink.resolve_roblox_id(member.guild.id, member.id)
            await cooldowns.bloxlink.spend()
        except BloxlinkRateLimitError as e:
            retry = f" in about {int(e.retry_after)}s" if e.retry_after is not None else ""
            return await interaction.failure(f"{CommonEmojis.FAIL} **Bloxlink is rate limited. Try again{retry}.**")
        except BloxlinkError:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **Bloxlink is unreachable right now. Try again later.**"
            )

        if roblox_id is None:
            return await interaction.failure(f"{CommonEmojis.FAIL} **You aren't verified through Bloxlink.**")

        # Validate the account exists, as the force command does.
        try:
            rblx = await service.roblox_user(roblox_id=roblox_id)
            await cooldowns.roblox.spend()
        except RobloxRateLimitError as e:
            retry = f" in about {int(e.retry_after)}s" if e.retry_after is not None else ""
            return await interaction.failure(f"{CommonEmojis.FAIL} **Roblox is rate limited. Try again{retry}.**")
        except RobloxError:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **The Roblox API is unreachable right now. Try again later.**"
            )

        if rblx is None:
            return await interaction.failure(f"{CommonEmojis.FAIL} **No Roblox user exists with id `{roblox_id}`.**")

        # Refuse when another player owns the account.  The reply names nobody -
        # who owns an account isn't something a user should learn here.
        existing = await service.profile_from_roblox_id(roblox_id)

        if existing is not None and existing.snowflake != member.id:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **That Roblox account is already linked to another Discord account.**"
            )

        profile = service.profile(member.id)
        player = await profile.ensure_player()
        previous_roblox_id = player.roblox_id

        try:
            await profile.link_roblox(roblox_id)
        except RecordNotFoundError:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **Your player row disappeared mid-update. Try again.**"
            )

        await cooldowns.weekly.spend(limit=1, duration=timedelta(days=7))

        # The link is written; nothing below may fail the command.
        headshot = await headshot_or_none(profile, roblox_id)

        embed = discord.Embed(
            description="# Member Self-Linked\n",
            color=Color.brand_green(),
        )

        add_discord_field(embed, member)
        add_roblox_field(embed, rblx, roblox_id, headshot, previous_roblox_id=previous_roblox_id)
        set_member_footer(embed, member)

        await send_roblox_log(self.bot, member.guild, embed)

        await interaction.success(
            f"{CommonEmojis.SUCCESS} **Linked your account to `{rblx.username}` (`{roblox_id}`).**"
        )


async def setup(bot: CircuitBot):
    await bot.add_cog(Update(bot))
