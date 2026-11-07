"""The teams module's service: every read and write over applications, teams, their log, and their roster."""

import datetime
import re
from typing import TYPE_CHECKING, Any

import aiohttp
import discord

from ...core.database import DatabaseConnection
from ...core.logging import get_logger
from ...core.service import Service
from .errors import TeamError
from .models import (
    ApplicationRow,
    CreateApplication,
    CreateOwnership,
    CreateTeam,
    CreateTransactionLog,
    RosterRow,
    TeamRow,
)

if TYPE_CHECKING:
    from ...core.bluerobot import CircuitBot

__all__ = ("TeamService",)

logger = get_logger("team_service")

# Discord emoji names allow only letters, digits, and underscores.
EMOJI_NAME_PATTERN = re.compile(r"[^0-9A-Za-z_]+")


class TeamService(Service):
    """Team reads and writes over the database, the guild, and the Discord objects a team owns.

    ``approve`` is the moment a team comes into existence: the role and emoji
    are created first, then the team, its ownership, and the application flip
    share one transaction.  Status transitions are conditional writes, so a
    concurrent decision wins instead of being overwritten.
    """

    def __init__(self, *, bot: "CircuitBot") -> None:
        super().__init__(bot=bot)

        self.application = self.get_repository(ApplicationRow)
        self.create_application = self.get_repository(CreateApplication)
        self.team = self.get_repository(TeamRow)
        self.create_team = self.get_repository(CreateTeam)
        self.create_ownership = self.get_repository(CreateOwnership)
        self.create_log = self.get_repository(CreateTransactionLog)
        self.roster_view = self.get_repository(RosterRow)

    # -- creation and the pending phase ------------------------------------

    async def create_pending(self, *, name: str, owner: int, previous_team_id: int | None = None) -> ApplicationRow:
        """Create *owner*'s pending application.

        The team, ownership, and log rows are all written at approval - a
        denied application leaves nothing behind.  A lineage link is refused
        unless the previous team is inactive and *owner* owned or coached it.
        """
        name = self.validate_name(name)

        async with self.bot.database.transaction() as conn:
            if previous_team_id is not None:
                await self.validate_lineage(conn, creator=owner, previous_team_id=previous_team_id)

            create_application = conn.get_repository(CreateApplication)
            applications = conn.get_repository(ApplicationRow)

            created = await create_application.insert(
                CreateApplication(name=name, owner=owner, previous_team_id=previous_team_id)
            )

            row = await applications.get_by_pk(created.id)

            if row is None:
                # Written by the insert above on this connection - only a bug gets here.
                raise RuntimeError(f"Inserted application {created.id} could not be read back in the same transaction")

            return row

    def validate_name(self, name: str) -> str:
        """Return *name* stripped, or refuse it - it becomes the application, role, and emoji name."""
        stripped = name.strip()

        if not 2 <= len(stripped) <= 32 or "\n" in stripped or "\t" in stripped:
            raise TeamError("Team names must be between 2 and 32 characters, with no tabs or newlines.")

        return stripped

    async def validate_lineage(self, conn: DatabaseConnection, *, creator: int, previous_team_id: int) -> None:
        """Refuse a lineage link the rules don't allow.

        The previous team must be inactive, and *creator* must have owned or
        coached it.
        """
        teams = conn.get_repository(TeamRow)

        previous = teams.from_record(await conn.fetchrow("SELECT * FROM teams WHERE id = $1", previous_team_id))

        if previous is None:
            raise TeamError("That team doesn't exist.")

        if previous.status != "inactive":
            raise TeamError("You can only link the history of an inactive team.")

        eligible = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM transaction_log "
            "WHERE player = $1 AND role = 'coach' AND (from_team_id = $2 OR to_team_id = $2)) "
            "OR EXISTS (SELECT 1 FROM team_ownership WHERE player = $1 AND team_id = $2)",
            creator,
            previous_team_id,
        )

        if not eligible:
            raise TeamError("You must have owned or coached that team to claim its history.")

    async def attach_logo(self, application: ApplicationRow, url: str) -> ApplicationRow | None:
        """Store *url* as *application*'s logo, or ``None`` if the row is gone.

        The URL is a cache - attachment URLs expire - so approval refetches
        it from the request message.
        """
        return await self.application.update(application, logo_url=url)

    async def attach_request_message(self, application: ApplicationRow, snowflake: int) -> ApplicationRow | None:
        """Store the pending-teams message *snowflake* on *application*, or ``None`` if the row is gone.

        The message is the approver queue handle - approval refetches the
        logo from it.
        """
        return await self.application.update(application, request_message_snowflake=snowflake)

    # -- the approver decisions --------------------------------------------

    async def approve(self, application: ApplicationRow) -> TeamRow | None:
        """Approve *application*: create its Discord role and emoji, then bring the team into existence.

        The role is required, so a failure to create it aborts the approval
        and the emoji is deleted again.  The emoji is best-effort: no logo,
        or an expired upload, means no emoji.  ``None`` means a concurrent
        deny won, and the role and emoji are cleaned up.
        """
        if application.status != "pending":
            raise TeamError("That application is no longer pending.")

        guild = self.bot.get_guild(self.bot.config.guild_id)

        if guild is None:
            raise TeamError("The league guild isn't available right now - try again later.")

        role: discord.Role | None = None
        emoji: discord.Emoji | None = None

        try:
            role = await guild.create_role(name=application.name)

            logo_url = await self.fresh_logo_url(application) or application.logo_url

            if logo_url is not None:
                emoji = await self.create_emoji(guild, application.name, logo_url)

            team = await self.approve_write(application, role, emoji, logo_url)
        except Exception:
            await self.cleanup(role, emoji)
            raise

        if team is None:
            # A concurrent deny won - take the role and emoji back down.
            await self.cleanup(role, emoji)

        return team

    async def approve_write(
        self,
        application: ApplicationRow,
        role: discord.Role,
        emoji: discord.Emoji | None,
        logo_url: str | None,
    ) -> TeamRow | None:
        """The database side of an approval: flip the application, then create the team and its ownership.

        All three writes share one transaction, and the flip is conditional
        on the application still being ``pending`` - ``None`` means a
        concurrent deny won.
        """
        async with self.bot.database.transaction() as conn:
            record = await conn.fetchrow(
                "UPDATE team_applications SET status = 'approved', decided_at = now(), updated_at = now() "
                "WHERE id = $1 AND status = 'pending' RETURNING *",
                application.id,
            )

            if record is None:
                return None

            create_team = conn.get_repository(CreateTeam)
            create_ownership = conn.get_repository(CreateOwnership)
            teams = conn.get_repository(TeamRow)

            created = await create_team.insert(
                CreateTeam(
                    name=application.name,
                    application_id=application.id,
                    role_snowflake=role.id,
                    emoji_snowflake=emoji.id if emoji is not None else None,
                    logo_url=logo_url,
                    previous_team_id=application.previous_team_id,
                )
            )

            team = await teams.get_by_pk(created.id)

            if team is None:
                # Written by the insert above on this connection - only a bug gets here.
                raise RuntimeError(f"Inserted team {created.id} could not be read back in the same transaction")

            await create_ownership.insert(CreateOwnership(team_id=team.id, player=application.owner))

            return team

    async def deny(self, application: ApplicationRow) -> ApplicationRow | None:
        """Refuse *application*, freeing its name and owner slot for a resubmission.

        Conditional on the row still being ``pending`` - ``None`` means a
        concurrent approve won.
        """
        record = await self.bot.database.fetchrow(
            "UPDATE team_applications SET status = 'denied', decided_at = now(), updated_at = now() "
            "WHERE id = $1 AND status = 'pending' RETURNING *",
            application.id,
        )

        return self.application.from_record(record)

    async def deactivate(self, team: TeamRow) -> TeamRow | None:
        """The team left the league: mark it ``inactive``, release its application's claims, and delete its Discord role and emoji.

        Everything else stays for history - the application flips to
        ``released``, freeing the name and owner slot for a successor.  The
        Discord deletions never raise, and the write is conditional on the
        team still being ``active``.
        """
        async with self.bot.database.transaction() as conn:
            record = await conn.fetchrow(
                "UPDATE teams SET status = 'inactive', inactivated_at = $2, updated_at = now() "
                "WHERE id = $1 AND status = 'active' RETURNING *",
                team.id,
                datetime.datetime.now(datetime.UTC),
            )

            updated = self.team.from_record(record)

            if updated is None:
                return None

            await conn.execute(
                "UPDATE team_applications SET status = 'released', updated_at = now() WHERE id = $1",
                team.application_id,
            )

        guild = self.bot.get_guild(self.bot.config.guild_id)

        if guild is not None:
            if team.role_snowflake is not None:
                role = guild.get_role(team.role_snowflake)

                if role is not None:
                    await self.delete_role(role)

            if team.emoji_snowflake is not None:
                emoji = discord.utils.get(guild.emojis, id=team.emoji_snowflake)

                if emoji is not None:
                    await self.delete_emoji(emoji)

        return updated

    # -- the roster ---------------------------------------------------------

    async def promote(self, *, owner: int, player: int) -> CreateTransactionLog | None:
        """Add *player* as a coach of *owner*'s active team, inferred from their current ownership.

        ``None`` means *owner* has no active team.
        """
        if player == owner:
            raise TeamError("You can't promote yourself - you're already the owner.")

        team = await self.find_by_owner(owner)

        if team is None:
            return None

        return await self.create_log.insert(
            CreateTransactionLog(player=player, to_team_id=team.id, action="promote", role="coach", actor=owner)
        )

    async def demote(self, *, owner: int, player: int) -> CreateTransactionLog | None:
        """Lower *player* on *owner*'s active team to a plain player.

        Mirrors :meth:`promote` - they stay on the roster as ``role IS NULL``
        while the log still shows they once coached.
        """
        if player == owner:
            raise TeamError("You can't demote the owner - ownership leaves with the team.")

        team = await self.find_by_owner(owner)

        if team is None:
            return None

        return await self.create_log.insert(
            CreateTransactionLog(player=player, to_team_id=team.id, action="demote", actor=owner)
        )

    # -- reads --------------------------------------------------------------

    async def find_by_name(self, name: str) -> TeamRow | None:
        """The active team named *name*, case-insensitively, or ``None``."""
        rows = await self.fetch_teams("SELECT * FROM teams WHERE lower(name) = lower($1) AND status = 'active'", name)

        return rows[0] if rows else None

    async def find_application_by_name(self, name: str) -> ApplicationRow | None:
        """The pending application named *name*, case-insensitively, or ``None``."""
        rows = await self.fetch_applications(
            "SELECT * FROM team_applications WHERE lower(name) = lower($1) AND status = 'pending'", name
        )

        return rows[0] if rows else None

    async def find_by_owner(self, snowflake: int) -> TeamRow | None:
        """The active team *snowflake* currently owns, or ``None`` - one live ownership per player."""
        rows = await self.fetch_teams(
            "SELECT t.* FROM teams t "
            "JOIN team_ownership o ON o.team_id = t.id "
            "WHERE o.player = $1 AND o.ended_at IS NULL AND t.status = 'active'",
            snowflake,
        )

        return rows[0] if rows else None

    async def pending(self) -> list[ApplicationRow]:
        """Every pending application, oldest first - the approver queue."""
        return await self.fetch_applications("SELECT * FROM team_applications WHERE status = 'pending' ORDER BY id")

    async def lineage(self, team: TeamRow) -> list[TeamRow]:
        """The chain behind *team*, immediate predecessor first.  Empty when the team stands alone."""
        return await self.fetch_teams(
            """
            WITH RECURSIVE chain AS (
                SELECT id, name, application_id, role_snowflake, emoji_snowflake, logo_url,
                       status, approved_at, inactivated_at, previous_team_id, created_at, updated_at,
                       0 AS depth
                FROM teams WHERE id = $1

                UNION ALL

                SELECT t.id, t.name, t.application_id, t.role_snowflake, t.emoji_snowflake, t.logo_url,
                       t.status, t.approved_at, t.inactivated_at, t.previous_team_id, t.created_at, t.updated_at,
                       c.depth + 1
                FROM teams t JOIN chain c ON t.id = c.previous_team_id
                WHERE c.depth < 64  -- stop a hand-edited cycle from looping forever
            )
            SELECT id, name, application_id, role_snowflake, emoji_snowflake, logo_url,
                   status, approved_at, inactivated_at, previous_team_id, created_at, updated_at
            FROM chain WHERE depth > 0 ORDER BY depth
            """,
            team.id,
        )

    async def previous_teams_for(self, player: int) -> list[TeamRow]:
        """The inactive teams *player* may link as history - ones they owned or coached.

        Feeds the ``previous_team`` autocomplete.
        """
        return await self.fetch_teams(
            "SELECT DISTINCT t.* FROM teams t "
            "WHERE t.status = 'inactive' AND ("
            "  EXISTS (SELECT 1 FROM transaction_log l "
            "          WHERE (l.from_team_id = t.id OR l.to_team_id = t.id) "
            "            AND l.player = $1 AND l.role = 'coach') "
            "  OR EXISTS (SELECT 1 FROM team_ownership o WHERE o.team_id = t.id AND o.player = $1)"
            ") "
            "ORDER BY t.id DESC",
            player,
        )

    async def owner(self, team: TeamRow) -> discord.Member | None:
        """The guild member who currently owns *team*, or ``None`` when they aren't in the guild."""
        record = await self.bot.database.fetchrow(
            "SELECT player FROM team_ownership WHERE team_id = $1 AND ended_at IS NULL", team.id
        )

        if record is None:
            return None

        owner = record["player"]

        guild = self.bot.get_guild(self.bot.config.guild_id)

        if guild is None:
            logger.warning(
                "Could not fetch owner %d of team %d: guild %d not found", owner, team.id, self.bot.config.guild_id
            )
            return None

        member = guild.get_member(owner)

        if member is not None:
            return member

        try:
            return await guild.fetch_member(owner)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.warning("Could not fetch owner %d of team %d: %s", owner, team.id, e)
            return None

    async def roster(self, team: TeamRow) -> list[RosterRow]:
        """The team's current membership, straight from the view."""
        return await self.roster_view.find_by(team_id=team.id)

    # -- helpers ------------------------------------------------------------

    async def fetch_teams(self, query: str, *args: Any) -> list[TeamRow]:
        """Run *query* and read every row back as a ``TeamRow``."""
        return self.team.from_records(await self.bot.database.fetch(query, *args))

    async def fetch_applications(self, query: str, *args: Any) -> list[ApplicationRow]:
        """Run *query* and read every row back as an ``ApplicationRow``."""
        return self.application.from_records(await self.bot.database.fetch(query, *args))

    async def fresh_logo_url(self, application: ApplicationRow) -> str | None:
        """The request message's first attachment URL, refetched so the signature is fresh.

        ``None`` when there is no message, it is gone, or it has no
        attachment - approval then proceeds without a logo.
        """
        if application.request_message_snowflake is None:
            return None

        channel = self.bot.get_channel(self.bot.config.league.pending_teams_channel_id)

        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Could not fetch request message for application %d: channel %d not found",
                application.id,
                self.bot.config.league.pending_teams_channel_id,
            )
            return None

        try:
            message = await channel.fetch_message(application.request_message_snowflake)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.warning("Could not fetch request message for application %d: %s", application.id, e)
            return None

        return message.attachments[0].url if message.attachments else None

    async def create_emoji(self, guild: discord.Guild, name: str, url: str) -> discord.Emoji | None:
        """Create a guild emoji from the image at *url*, named for the team.

        Never raises - a failed download or create logs and returns ``None``.
        """
        try:
            async with self.bot.session.get(url) as response:
                response.raise_for_status()
                image = await response.read()

            return await guild.create_custom_emoji(name=EMOJI_NAME_PATTERN.sub("_", name)[:32], image=image)
        except (aiohttp.ClientError, discord.HTTPException) as e:
            logger.warning("Could not create emoji for team %r: %s", name, e)
            return None

    async def cleanup(self, role: discord.Role | None, emoji: discord.Emoji | None) -> None:
        """Delete *role* and *emoji*, never raising - the undo after a failed approval."""
        if role is not None:
            await self.delete_role(role)

        if emoji is not None:
            await self.delete_emoji(emoji)

    async def delete_role(self, role: discord.Role) -> None:
        """Delete *role*, never raising."""
        try:
            await role.delete()
        except discord.HTTPException as e:
            logger.warning("Could not delete role %d: %s", role.id, e)

    async def delete_emoji(self, emoji: discord.Emoji) -> None:
        """Delete *emoji*, never raising."""
        try:
            await emoji.delete()
        except discord.HTTPException as e:
            logger.warning("Could not delete emoji %d: %s", emoji.id, e)
