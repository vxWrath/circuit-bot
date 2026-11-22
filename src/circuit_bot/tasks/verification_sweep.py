"""Catch members who joined while the bot was offline: one sweep per startup."""

import asyncio
import itertools

import discord
from discord.ext import commands

from ..core import BloxlinkError, CircuitBot, create_logged_task, get_logger
from ..modules import PlayerService

logger = get_logger("verification_sweep")

# The batch is the concurrency unit: one gather of BATCH_SIZE checks, so the
# API sees at most this many requests at a time.
BATCH_SIZE = 10

# Bloxlink failures that stop the sweep, so an outage doesn't turn into one
# failed request per unchecked member.
FAILURE_LIMIT = 10


class VerificationSweep(commands.Cog):
    """Ask Bloxlink about every unchecked member, once, in the background."""

    def __init__(self, bot: CircuitBot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Start the sweep when the cog loads, once per process.

        The cogs load once at startup, where on_ready fires again on every
        full reconnect and would start a second sweep.  The task itself
        waits for the bot to be ready, then chunks the guild.
        """
        create_logged_task(self.sweep(), name="verification-sweep")

    async def sweep(self) -> None:
        """Chunk the guild, then check every member the database hasn't marked yet."""
        await self.bot.wait_until_ready()

        guild = self.bot.get_guild(self.bot.config.guild_id)

        if guild is None:
            logger.warning("Startup sweep skipped: guild %d not found", self.bot.config.guild_id)
            return

        if not guild.chunked:
            try:
                await guild.chunk()
            except discord.HTTPException as e:
                logger.warning("Startup sweep skipped: could not chunk guild %d: %s", guild.id, e)
                return

        service = self.bot.get_service(PlayerService)

        # One read for the whole players table instead of one per member, so
        # the marker check happens in memory.  shortcut: whole-table read,
        # fine while the table holds one guild's members.
        players = {player.snowflake: player for player in await service.all_players()}

        # The verified role alone doesn't say the member is linked: a member
        # who verified while the bot was offline holds the role but was never
        # linked.  The marker decides what to re-ask, not the role.
        unchecked = [
            member for member in guild.members
            if not member.bot and (member.id not in players or players[member.id].verified_checked_at is None)
        ]

        missing = [member.id for member in unchecked if member.id not in players]

        if missing:
            # The join handler can create the same row mid-sweep, so skip the
            # conflict instead of failing on it.
            await service.create_many(missing)

        checked = 0
        linked = 0
        failures = 0

        for batch in itertools.batched(unchecked, BATCH_SIZE, strict=True):
            results = await asyncio.gather(
                *(service.link_roblox_from_bloxlink(member) for member in batch),
                return_exceptions=True,
            )

            marked: list[int] = []

            for member, result in zip(batch, results, strict=True):
                if isinstance(result, BloxlinkError):
                    failures += 1
                    logger.warning("Startup sweep could not check %d: %s", member.id, result)
                elif isinstance(result, BaseException):
                    # A write race, not an API failure - leaving the member
                    # unmarked makes the next startup re-ask, which converges.
                    logger.error("Startup sweep failed for %d", member.id, exc_info=result)
                else:
                    marked.append(member.id)
                    checked += 1

                    if result:
                        linked += 1

            if marked:
                # One statement stamps the whole batch.
                await service.mark_many_verified_checked(marked)

            if failures >= FAILURE_LIMIT:
                logger.warning("Startup sweep stopped after %d Bloxlink failures", failures)
                return

        logger.info("Startup sweep finished: %d members checked, %d linked", checked, linked)


async def setup(bot: CircuitBot):
    await bot.add_cog(VerificationSweep(bot))
