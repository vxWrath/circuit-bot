import asyncio
from datetime import timedelta

import discord
from discord.ext import commands

from ..core import CircuitBot, Color, get_logger
from ..modules import PlayerService

logger = get_logger("on_member_join")

BLOXLINK_BOT_USER = discord.Object(id=426537812993638400, type=discord.User)


class OnMemberJoin(commands.Cog):
    def __init__(self, bot: CircuitBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        service = self.bot.get_service(PlayerService)

        profile = service.profile(member.id)
        await profile.ensure_player()

        def check_for_verified_role(_: discord.Member, after: discord.Member) -> bool:
            return (
                member.guild.id == after.guild.id
                and member.id == after.id
                and after.get_role(self.bot.config.verified_role_id) is not None
            )

        def added_verified_role(log: discord.AuditLogEntry) -> bool:
            return (
                log.target is not None
                and log.target.id == member.id
                and any(role.id == self.bot.config.verified_role_id for role in getattr(log.after, "roles", ()))
            )

        # Listen before querying the audit log, so an update landing during the
        # query isn't missed by both.
        verified = asyncio.create_task(self.bot.wait_for("member_update", check=check_for_verified_role, timeout=20))

        # the off chance that this event fired late and bloxlink already
        # updated the member's roles
        after = (
            (member.joined_at - timedelta(seconds=3))
            if member.joined_at
            else (discord.utils.utcnow() - timedelta(minutes=1))
        )
        logs = [
            log
            async for log in member.guild.audit_logs(
                user=BLOXLINK_BOT_USER,
                action=discord.AuditLogAction.member_role_update,
                after=after,
                limit=None,
                oldest_first=False,
            )
            if added_verified_role(log)
        ]

        if logs:
            verified.cancel()
        else:
            try:
                await verified
            except TimeoutError:
                return

        # Bloxlink updated the member's roles, so we can safely assume they're verified.
        bloxlink = self.bot.bloxlink
        roblox_id = await bloxlink.resolve_roblox_id(member.guild.id, member.id)

        if roblox_id is None:
            logger.debug(f"Could not resolve Roblox ID for {member.id}. Likely not verified through Bloxlink.")
            return

        await profile.link_roblox(roblox_id)
        logger.debug(f"Linked {member.id} to Roblox ID {roblox_id}")

        rblx = await profile.roblox_user()
        hs_url = await profile.headshot_url()

        if not rblx or not hs_url:
            logger.debug(f"Could not fetch roblox account info for roblox id {roblox_id}")
            return

        channel_id = self.bot.config.roblox_link_log_channel_id

        if not channel_id:
            return

        channel = member.guild.get_channel(channel_id) or await member.guild.fetch_channel(channel_id)

        if channel.type != discord.ChannelType.text:
            return

        embed = discord.Embed(
            title="New Member Verified & Linked",
            color=Color.brand_green(),
        )
        embed.add_field(
            name="Discord",
            value=(
                f"`{member.id}`\n"
                f"`Username:` **{member.name}**\n"
                f"`Mention:` {member.mention}\n"
                f"`Joined:` {discord.utils.format_dt(member.joined_at, style='R') if member.joined_at else '**Guest**'}\n"
                f"`Created:` {discord.utils.format_dt(member.created_at, style='R')}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Roblox",
            value=(
                f"`{rblx.id}`\n"
                f"`Username:` **{rblx.username}**\n"
                f"`Display Name:` **{rblx.display_name}**\n"
                f"`Created:` {discord.utils.format_dt(rblx.created_at, style='R')}"
            ),
            inline=True,
        )
        embed.set_thumbnail(url=hs_url)

        await channel.send(embed=embed)


async def setup(bot: CircuitBot):
    await bot.add_cog(OnMemberJoin(bot))
