import asyncio
from datetime import timedelta

import discord
from discord import SeparatorSpacing
from discord.ext import commands

from ..core import CircuitBot, Color, LayoutBuilder, RobloxError, get_logger
from ..modules import PlayerService, add_discord_section, add_roblox_section, send_roblox_log

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

            container = (
                LayoutBuilder()
                .container(accent_color=Color.brand_red())
                .text("## Duplicate Roblox Link Blocked")
                .separator(spacing=SeparatorSpacing.large)
            )

            add_discord_section(container, member)
            container.separator(spacing=SeparatorSpacing.large)
            add_roblox_section(container, rblx, roblox_id, None)
            container.separator(spacing=SeparatorSpacing.large)
            if owner is not None:
                add_discord_section(container, owner, title="Current Owner")
            else:
                container.text(f"`Current Owner:` `{existing.snowflake}`")

            await send_roblox_log(self.bot, member.guild, container.done())
            return

        await profile.link_roblox(roblox_id)
        logger.debug(f"Linked {member.id} to Roblox ID {roblox_id}")

        hs_url = await profile.headshot_url()

        if not hs_url:
            logger.debug(f"Could not fetch roblox headshot for roblox id {roblox_id}")
            return

        container = (
            LayoutBuilder()
            .container(accent_color=Color.brand_green())
            .text("## New Member Verified & Linked")
            .separator(spacing=SeparatorSpacing.large)
        )

        add_discord_section(container, member)
        container.separator(spacing=SeparatorSpacing.large)
        add_roblox_section(container, rblx, roblox_id, hs_url)

        await send_roblox_log(self.bot, member.guild, container.done())


async def setup(bot: CircuitBot):
    await bot.add_cog(OnMemberJoin(bot))
