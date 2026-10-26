import asyncio
from datetime import timedelta

import discord
from discord.ext import commands

from ..core import CircuitBot, Color, RobloxError, get_logger
from ..modules import (
    PlayerService,
    add_roblox_field,
    send_roblox_log,
)

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

        # The same guards the force command runs: don't link an account that
        # doesn't exist, or one another player already owns - the table's unique
        # constraint would fail the write anyway, this just skips it cleanly.
        try:
            rblx = await service.roblox_user(roblox_id=roblox_id)
        except RobloxError as e:
            logger.warning("Could not validate Roblox id %d for %d: %s", roblox_id, member.id, e)
            return

        if rblx is None:
            logger.debug(f"Roblox id {roblox_id} for {member.id} doesn't exist; skipping link.")
            return

        existing = await service.profile_from_roblox_id(roblox_id)

        if existing is not None and existing.snowflake != member.id:
            logger.debug(
                f"Roblox id {roblox_id} for {member.id} is already linked to {existing.snowflake}; skipping link."
            )

            owner = member.guild.get_member(existing.snowflake)

            embed = discord.Embed(
                description=(
                    "# Duplicate Roblox Link Blocked\n"
                    f"-# New member: {member.name} · {member.id}\n"
                    f"-# Existing member: "
                    f"{owner.name} · {owner.id}"
                    if owner is not None
                    else f"{existing.snowflake}"
                ),
                color=Color.brand_red(),
            )
            add_roblox_field(embed, rblx, roblox_id, None)

            await send_roblox_log(self.bot, member.guild, embed)
            return

        await profile.link_roblox(roblox_id)
        logger.debug(f"Linked {member.id} to Roblox ID {roblox_id}")

        hs_url = await profile.headshot_url()

        if not hs_url:
            logger.debug(f"Could not fetch roblox headshot for roblox id {roblox_id}")
            return

        embed = discord.Embed(
            description=f"# New Member Verified & Linked\n-# {member.name} · {member.id}\n",
            color=Color.brand_green(),
        )
        add_roblox_field(embed, rblx, roblox_id, hs_url)

        await send_roblox_log(self.bot, member.guild, embed)


async def setup(bot: CircuitBot):
    await bot.add_cog(OnMemberJoin(bot))
