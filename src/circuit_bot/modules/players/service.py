from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord

from ...core import Color
from ...core.logging import get_logger
from ...core.roblox import RobloxError, RobloxUser
from ...core.service import Service
from . import roblox
from .models import CreatePlayer, PlayerRow
from .profile import PlayerProfile

if TYPE_CHECKING:
    from ...core.bluerobot import CircuitBot

__all__ = ("PlayerService",)

logger = get_logger("player_service")


class PlayerService(Service):
    """Player reads and writes over the database and the cached clients.

    The one place that composes a player out of the row in Postgres, the Roblox
    account, and the guild member, so commands have a single surface to ask.  A
    method spanning several statements opens the
    transaction itself and builds its repositories on it, so a caller can't
    half-commit it.
    """

    def __init__(self, *, bot: "CircuitBot") -> None:
        super().__init__(bot=bot)

        self.player = self.get_repository(PlayerRow)
        self.create_player = self.get_repository(CreatePlayer)

    def profile(self, member: discord.Member | int) -> PlayerProfile:
        """Open a player's profile for *member* or their snowflake.  Touches nothing until a part is asked for.

        A member handed over is seeded into the profile, so the guild lookup
        is saved.
        """
        if isinstance(member, discord.Member):
            profile = PlayerProfile(self, member.id)
            profile.set_part(discord.Member, member)
            return profile

        return PlayerProfile(self, member)

    async def find_player(self, *, snowflake: int) -> PlayerRow | None:
        """The player with *snowflake*, or ``None`` if there's none."""
        players = await self.player.find_by(snowflake=snowflake)
        return players[0] if players else None

    async def profile_from_roblox_id(self, roblox_id: int) -> PlayerProfile | None:
        """Open the profile of the player linked to *roblox_id*, or ``None`` when there's no such player.

        Unlike :meth:`profile`, this one has to read the row to learn the
        snowflake the profile is keyed by.  The row it read is seeded into the
        profile, so asking for it again costs nothing.
        """
        players = await self.player.find_by(roblox_id=roblox_id)

        if not players:
            return None

        row = players[0]

        profile = self.profile(row.snowflake)
        profile.set_part(PlayerRow, row)

        return profile

    async def create(self, *, snowflake: int, roblox_id: int | None = None) -> CreatePlayer:
        """Add a player, returning the row the database read back."""
        return await self.create_player.insert(CreatePlayer(snowflake=snowflake, roblox_id=roblox_id))

    async def link_roblox(self, player: PlayerRow, roblox_id: int) -> PlayerRow | None:
        """Attach a Roblox account to *player*, or ``None`` if the row is gone.

        *player* is the row the write is keyed by, so a caller that already read
        the player passes that read.
        """

        return await self.player.update(player, roblox_id=roblox_id)

    async def unlink_roblox(self, player: PlayerRow) -> PlayerRow | None:
        """Drop the Roblox account on *player*, or ``None`` if the row is gone.

        Mirrors :meth:`link_roblox` - *player* is the row the write is keyed by.
        """

        return await self.player.update(player, roblox_id=None)

    async def roblox_user(self, *, roblox_id: int) -> RobloxUser | None:
        """The Roblox user info of *roblox_id*, or ``None`` when there's no such user."""
        return await self.bot.roblox.get_user(roblox_id)

    async def headshot_url(self, *, roblox_id: int) -> str | None:
        """The headshot of *roblox_id*, or ``None`` when there's no such user."""
        return await self.bot.roblox.get_headshot(roblox_id)

    async def member(self, *, snowflake: int) -> discord.Member | None:
        """The member with *snowflake* in the league guild, or ``None`` when they aren't there."""
        guild = self.bot.get_guild(self.bot.config.guild_id)

        if guild is None:
            logger.warning("Could not fetch member %d: guild %d not found", snowflake, self.bot.config.guild_id)
            return None

        member = guild.get_member(snowflake)

        if member is not None:
            return member

        # The member cache keeps only members seen joining, so ask Discord.
        try:
            return await guild.fetch_member(snowflake)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.warning("Could not fetch member %d: %s", snowflake, e)
            return None

    async def set_nickname(self, member: discord.Member, nick: str | None) -> None:
        """Set *member*'s nickname to *nick*, never raising."""
        try:
            await member.edit(nick=nick)
        except discord.HTTPException as e:
            logger.warning("Could not set nickname for %d: %s", member.id, e)

    async def link_roblox_from_bloxlink(self, member: discord.Member) -> bool:
        """Resolve *member*'s Bloxlink account and link it, guarded like the force command.

        A member with no Bloxlink account, an account that doesn't exist on
        Roblox, or one another player already owns is left unlinked.  The
        member must have a players row - callers ensure it.  Returns whether
        the member was linked.
        """
        roblox_id = await self.bot.bloxlink.resolve_roblox_id(member.guild.id, member.id)

        if roblox_id is None:
            logger.debug(f"Could not resolve Roblox ID for {member.id}. Likely not verified through Bloxlink.")
            return False

        # The same guards the force command runs: don't link an account that
        # doesn't exist, or one another player already owns - the table's unique
        # constraint would fail the write anyway, this just skips it cleanly.
        try:
            rblx = await self.roblox_user(roblox_id=roblox_id)
        except RobloxError as e:
            logger.warning("Could not validate Roblox id %d for %d: %s", roblox_id, member.id, e)
            return False

        if rblx is None:
            logger.debug(f"Roblox id {roblox_id} for {member.id} doesn't exist; skipping link.")
            return False

        existing = await self.profile_from_roblox_id(roblox_id)

        if existing is not None and existing.snowflake != member.id:
            logger.debug(
                f"Roblox id {roblox_id} for {member.id} is already linked to {existing.snowflake}; skipping link."
            )

            owner = member.guild.get_member(existing.snowflake)

            embed = discord.Embed(
                description=(
                    "# Duplicate Roblox Link Blocked\n"
                    f"-# New member: {member.name} • {member.id}\n"
                    f"-# Existing member: "
                    f"{owner.name} • {owner.id}"
                    if owner is not None
                    else f"{existing.snowflake}"
                ),
                color=Color.brand_red(),
            )
            roblox.add_field(embed, rblx, roblox_id, None)

            await roblox.send_log(self.bot, member.guild, embed)
            return False

        profile = self.profile(member)
        await profile.link_roblox(roblox_id)
        logger.debug(f"Linked {member.id} to Roblox ID {roblox_id}")

        hs_url = await profile.roblox.headshot_or_none()

        if not hs_url:
            logger.debug(f"Could not fetch roblox headshot for roblox id {roblox_id}")
            return True

        embed = roblox.build_embed(
            "New Member Verified & Linked",
            member,
            Color.brand_green(),
            rblx,
            roblox_id,
            hs_url,
        )
        await roblox.send_log(self.bot, member.guild, embed)
        return True

    async def all_players(self) -> list[PlayerRow]:
        """Every player row in one read - the startup sweep builds its marker map from this."""
        return self.player.from_records(await self.bot.database.fetch("SELECT * FROM players"))

    async def create_many(self, snowflakes: Sequence[int]) -> None:
        """Create a row for every listed snowflake, skipping ones a concurrent join created.

        The startup sweep's batch path: one call instead of a write per member.
        """
        if not snowflakes:
            return

        await self.bot.database.executemany(
            "INSERT INTO players (snowflake) VALUES ($1) ON CONFLICT (snowflake) DO NOTHING",
            [(snowflake,) for snowflake in snowflakes],
        )

    async def mark_many_verified_checked(self, snowflakes: Sequence[int]) -> None:
        """Stamp every listed player as checked by the startup sweep, in one statement."""
        if not snowflakes:
            return

        await self.bot.database.execute(
            "UPDATE players SET verified_checked_at = now(), updated_at = now() WHERE snowflake = ANY($1)",
            list(snowflakes),
        )
