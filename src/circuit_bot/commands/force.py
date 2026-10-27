from datetime import timedelta

import discord
from discord.app_commands import describe
from discord.ext import commands

from ..core import (
    CircuitBot,
    CircuitGroup,
    Color,
    CommonEmojis,
    DeferOptions,
    Interaction,
    InteractionOptions,
    RobloxError,
    developer_only,
    get_logger,
    shared_cooldown,
)
from ..modules import PlayerService, roblox

logger = get_logger("force")

DEFER = {"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))}


class Force(commands.Cog):
    def __init__(self, bot: CircuitBot):
        self.bot = bot

    force = CircuitGroup(name="force", description="Developer-only utilities.", guild_only=True)
    # roblox_group, not roblox, so the roblox toolkit imported above keeps the plain name.
    roblox_group = CircuitGroup(
        parent=force, name="roblox", description="Force changes to a player's linked Roblox account.", guild_only=True
    )
    bloxlink = CircuitGroup(
        parent=force,
        name="bloxlink",
        description="Update a player's linked account from Bloxlink's records.",
        guild_only=True,
    )

    @roblox_group.command(name="update", description="Set a player's linked Roblox account.", extras=DEFER)
    @describe(member="The member to update", roblox_id="The Roblox ID to link")
    @developer_only()
    @shared_cooldown("roblox", limit=1, duration=timedelta(minutes=1))
    async def roblox_update(self, interaction: Interaction, member: discord.Member, roblox_id: int):
        await self.link_roblox(interaction, member, roblox_id)

    @roblox_group.command(name="unlink", description="Drop a player's linked Roblox account.", extras=DEFER)
    @describe(member="The member to update")
    @developer_only()
    async def roblox_unlink(self, interaction: Interaction, member: discord.Member):
        service = self.bot.get_service(PlayerService)
        profile = service.profile(member)
        player = await profile.player()

        if player is None:
            return await interaction.failure(f"{CommonEmojis.FAIL} **No player row exists for {member.mention}.**")

        if player.roblox_id is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **{member.mention} isn't linked to a Roblox account.**"
            )

        previous_roblox_id = player.roblox_id

        # The removed account's profile, for the audit embed - a failure only
        # leaves the embed thinner, it can't fail the command.
        try:
            rblx = await profile.roblox.user()
        except RobloxError as e:
            logger.warning("Could not fetch Roblox user %d before unlinking: %s", previous_roblox_id, e)
            rblx = None

        headshot = await profile.roblox.headshot_or_none()

        await profile.unlink_roblox()

        # The unlink is written; nothing below may fail the command.
        await profile.reset_nickname()

        invoker = interaction.require_member()

        embed = roblox.build_embed(
            "Member Forcibly Unlinked",
            member,
            Color.brand_red(),
            rblx,
            previous_roblox_id,
            headshot,
            field_title="Removed Roblox",
            enforcer=invoker,
        )
        await roblox.send_log(self.bot, member.guild, embed)
        await interaction.success(f"{CommonEmojis.SUCCESS} **Unlinked {member.mention} from `{previous_roblox_id}`.**")

    @bloxlink.command(
        name="update", description="Update a player's linked Roblox account from Bloxlink's records.", extras=DEFER
    )
    @describe(member="The member to update")
    @developer_only()
    @shared_cooldown("bloxlink", limit=1, duration=timedelta(minutes=1))
    @shared_cooldown("roblox", limit=1, duration=timedelta(minutes=1))
    async def bloxlink_update(self, interaction: Interaction, member: discord.Member):
        cooldowns = interaction.cooldowns

        resolved = await self.bot.bloxlink.resolve_roblox_id(member.guild.id, member.id)
        await cooldowns.bloxlink.spend()

        if resolved is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **{member.mention} isn't verified through Bloxlink.**"
            )

        await self.link_roblox(interaction, member, resolved)

    async def link_roblox(
        self,
        interaction: Interaction,
        member: discord.Member,
        roblox_id: int,
    ):
        """Validate *roblox_id*, guard the unique link, write it for *member*,
        and rename the member to Bloxlink's nickname format.
        """
        service = self.bot.get_service(PlayerService)
        cooldowns = interaction.cooldowns

        # Validate the account exists.  The embed needs the Roblox profile
        # anyway, and refusing a deleted account before the write beats linking
        # one.  The result is cached, so this is cheap.
        rblx = await service.roblox_user(roblox_id=roblox_id)
        await cooldowns.roblox.spend()

        if rblx is None:
            return await interaction.failure(f"{CommonEmojis.FAIL} **No Roblox user exists with id `{roblox_id}`.**")

        # Refuse when another player already owns the account - the table's
        # unique constraint would fail the write anyway, this just says so nicely.
        existing = await service.profile_from_roblox_id(roblox_id)

        if existing is not None and existing.snowflake != member.id:
            owner = member.guild.get_member(existing.snowflake)
            owner_text = owner.mention if owner is not None else f"`{existing.snowflake}`"

            return await interaction.failure(
                f"{CommonEmojis.FAIL} **Roblox account `{roblox_id}` is already linked to {owner_text}.**"
            )

        profile = service.profile(member)
        player = await profile.ensure_player()
        previous_roblox_id = player.roblox_id

        await profile.link_roblox(roblox_id)

        # The link is written; nothing below may fail the command.
        headshot = await profile.roblox.headshot_or_none()
        await profile.set_nickname_from_roblox()

        invoker = interaction.require_member()

        embed = roblox.build_embed(
            "Member Force-Linked",
            member,
            Color.brand_green(),
            rblx,
            roblox_id,
            headshot,
            previous_roblox_id=previous_roblox_id,
            enforcer=invoker,
        )
        await roblox.send_log(self.bot, member.guild, embed)

        await interaction.success(
            f"{CommonEmojis.SUCCESS} **Linked {member.mention} to `{rblx.username}` (`{roblox_id}`).**"
        )


async def setup(bot: CircuitBot):
    await bot.add_cog(Force(bot))
