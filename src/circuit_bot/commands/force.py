import discord
from discord.app_commands import describe
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
    developer_only,
    get_logger,
)
from ..modules import (
    PlayerService,
    discord_member_field,
    headshot_or_none,
    roblox_field,
    send_roblox_log,
)

logger = get_logger("force")

DEFER = {"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))}


class Force(commands.Cog):
    def __init__(self, bot: CircuitBot):
        self.bot = bot

    force = CircuitGroup(name="force", description="Developer-only utilities.", guild_only=True)
    roblox = CircuitGroup(
        parent=force, name="roblox", description="Force changes to a player's linked Roblox account.", guild_only=True
    )
    bloxlink = CircuitGroup(
        parent=force,
        name="bloxlink",
        description="Update a player's linked account from Bloxlink's records.",
        guild_only=True,
    )

    @roblox.command(name="update", description="Set a player's linked Roblox account.", extras=DEFER)
    @describe(member="The member to update", roblox_id="The Roblox ID to link")
    @developer_only()
    async def roblox_update(self, interaction: Interaction, member: discord.Member, roblox_id: int):
        await self.link_roblox(interaction, member, roblox_id)

    @roblox.command(name="unlink", description="Drop a player's linked Roblox account.", extras=DEFER)
    @describe(member="The member to update")
    @developer_only()
    async def roblox_unlink(self, interaction: Interaction, member: discord.Member):
        service = self.bot.get_service(PlayerService)
        profile = service.profile(member.id)
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
            rblx = await profile.roblox_user()
        except RobloxError as e:
            logger.warning("Could not fetch Roblox user %d before unlinking: %s", previous_roblox_id, e)
            rblx = None

        headshot = await headshot_or_none(profile, previous_roblox_id)

        try:
            await profile.unlink_roblox()
        except RecordNotFoundError:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **{member.mention}'s player row disappeared mid-update. Try again.**"
            )

        invoker = interaction.require_member()

        embed = discord.Embed(title="Account Unlinked", color=Color.brand_red())
        embed.add_field(name="Discord", value=discord_member_field(member), inline=True)
        embed.add_field(name="Removed Roblox", value=roblox_field(rblx, previous_roblox_id), inline=True)
        embed.add_field(name="Invoker", value=discord_member_field(invoker), inline=True)

        if headshot is not None:
            embed.set_thumbnail(url=headshot)

        await send_roblox_log(self.bot, member.guild, embed)
        await interaction.success(f"{CommonEmojis.SUCCESS} **Unlinked {member.mention} from `{previous_roblox_id}`.**")

    @bloxlink.command(
        name="update", description="Update a player's linked Roblox account from Bloxlink's records.", extras=DEFER
    )
    @describe(member="The member to update")
    @developer_only()
    async def bloxlink_update(self, interaction: Interaction, member: discord.Member):
        try:
            resolved = await self.bot.bloxlink.resolve_roblox_id(member.guild.id, member.id)
        except BloxlinkRateLimitError as e:
            retry = f" in about {int(e.retry_after)}s" if e.retry_after is not None else ""
            return await interaction.failure(f"{CommonEmojis.FAIL} **Bloxlink is rate limited. Try again{retry}.**")
        except BloxlinkError:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **Bloxlink is unreachable right now. Try again later.**"
            )

        if resolved is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **{member.mention} isn't verified through Bloxlink.**"
            )

        await self.link_roblox(interaction, member, resolved)

    async def link_roblox(self, interaction: Interaction, member: discord.Member, roblox_id: int):
        """Validate *roblox_id*, guard the unique link, and write it for *member*."""
        service = self.bot.get_service(PlayerService)

        # Validate the account exists.  The embed needs the Roblox profile
        # anyway, and refusing a deleted account before the write beats linking
        # one.  The result is cached, so this is cheap.
        try:
            rblx = await service.roblox_user(roblox_id=roblox_id)
        except RobloxRateLimitError as e:
            retry = f" in about {int(e.retry_after)}s" if e.retry_after is not None else ""
            return await interaction.failure(f"{CommonEmojis.FAIL} **Roblox is rate limited. Try again{retry}.**")
        except RobloxError:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **The Roblox API is unreachable right now. Try again later.**"
            )

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

        profile = service.profile(member.id)
        player = await profile.ensure_player()
        previous_roblox_id = player.roblox_id

        try:
            await profile.link_roblox(roblox_id)
        except RecordNotFoundError:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **{member.mention}'s player row disappeared mid-update. Try again.**"
            )

        # The link is written; nothing below may fail the command.
        headshot = await headshot_or_none(profile, roblox_id)
        invoker = interaction.require_member()

        embed = discord.Embed(title="Account Force-Linked", color=Color.brand_green())
        embed.add_field(name="Discord", value=discord_member_field(member), inline=True)
        embed.add_field(name="Roblox", value=roblox_field(rblx), inline=True)
        embed.add_field(
            name="Previous Roblox",
            value=f"`{previous_roblox_id}`" if previous_roblox_id is not None else "**None**",
            inline=True,
        )
        embed.add_field(name="Invoker", value=discord_member_field(invoker), inline=True)

        if headshot is not None:
            embed.set_thumbnail(url=headshot)

        await send_roblox_log(self.bot, member.guild, embed)

        await interaction.success(
            f"{CommonEmojis.SUCCESS} **Linked {member.mention} to `{rblx.username}` (`{roblox_id}`).**"
        )


async def setup(bot: CircuitBot):
    await bot.add_cog(Force(bot))
