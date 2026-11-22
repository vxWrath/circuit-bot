import asyncio
from datetime import timedelta

import discord
from discord.ext import commands

from ..core import CircuitBot, get_logger
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

        profile = service.profile(member)
        await profile.ensure_player()

        verified_role_id = self.bot.config.league.verified_role_id

        def check_for_verified_role(_: discord.Member, after: discord.Member) -> bool:
            return (
                member.guild.id == after.guild.id
                and member.id == after.id
                and after.get_role(verified_role_id) is not None
            )

        def added_verified_role(log: discord.AuditLogEntry) -> bool:
            return (
                log.target is not None
                and log.target.id == member.id
                and any(role.id == verified_role_id for role in getattr(log.after, "roles", ()))
            )

        # Listen before querying the audit log, so an update landing during the
        # query isn't missed by both.  Bloxlink can take a while to update a
        # new member's roles, so listen long enough for it to get around to it.
        verified = asyncio.create_task(self.bot.wait_for("member_update", check=check_for_verified_role, timeout=120))

        # Covers the off chance this event fired late, after Bloxlink already
        # updated the member's roles.
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
        await service.link_roblox_from_bloxlink(member)


async def setup(bot: CircuitBot):
    await bot.add_cog(OnMemberJoin(bot))
