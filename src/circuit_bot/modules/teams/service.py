"""The teams module's service: every read and write over applications, teams, their log, and their roster."""

import difflib
import re
from typing import TYPE_CHECKING, Any

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

__all__ = ("TeamService", "validate_team_color", "validate_team_name")

logger = get_logger("team_service")

# Two claimed names this alike read as the same team to a member.
SIMILARITY_THRESHOLD = 0.85


def validate_team_name(name: str) -> str:
    """Return *name* stripped, or refuse it - it becomes the application, role, and emoji name."""
    stripped = name.strip()

    if not 2 <= len(stripped) <= 32 or "\n" in stripped or "\t" in stripped:
        raise TeamError("Team names must be between 2 and 32 characters, with no tabs or newlines.")

    return stripped


def validate_team_color(color: int) -> None:
    """Refuse a color Discord roles can't take - it becomes the team's role color at approval."""
    if not isinstance(color, int) or isinstance(color, bool) or not 0 <= color <= 0xFFFFFF:
        raise TeamError("Team colors must be a whole number between 0 and 16777215 (#FFFFFF).")


def names_too_similar(new_name: str, claimed_name: str) -> bool:
    """Whether a member would read *claimed_name* and *new_name* as the same team.

    Names are compared lowercase with whitespace collapsed.  Identical names
    return False - the unique index owns the exact-match reply.
    """
    new = re.sub(r"\s+", " ", new_name.lower())
    claimed = re.sub(r"\s+", " ", claimed_name.lower())

    if new == claimed:
        return False

    return difflib.SequenceMatcher(None, new, claimed).ratio() >= SIMILARITY_THRESHOLD


class TeamService(Service):
    """Team reads and writes over the database, the guild, and the Discord objects a team owns."""

    def __init__(self, *, bot: "CircuitBot") -> None:
        super().__init__(bot=bot)

        self.application = self.get_repository(ApplicationRow)
        self.team = self.get_repository(TeamRow)
        self.create_application = self.get_repository(CreateApplication)
        self.create_team = self.get_repository(CreateTeam)
        self.create_ownership = self.get_repository(CreateOwnership)
        self.create_log = self.get_repository(CreateTransactionLog)
        self.roster_view = self.get_repository(RosterRow)

    async def register_application(
        self,
        *,
        name: str,
        owner: int,
        color: int,
        previous_team_id: int | None = None,
    ) -> ApplicationRow:
        """Create *owner*'s pending application.

        The team, ownership, and log rows are all written at approval - a
        denied application leaves nothing behind.  ``color`` becomes the
        team's role color at approval.
        """
        name = validate_team_name(name)
        validate_team_color(color)

        async with self.bot.database.transaction() as conn:
            if previous_team_id is not None:
                await validate_lineage(conn, creator=owner, previous_team_id=previous_team_id)

            # The unique index owns exact matches; this refuses the near ones.
            claimed = await conn.fetch("SELECT name FROM team_applications WHERE status IN ('pending', 'approved')")

            for record in claimed:
                if names_too_similar(name, record["name"]):
                    raise TeamError("That team name is too similar to an existing team.")

            create_application = conn.get_repository(CreateApplication)
            applications = conn.get_repository(ApplicationRow)

            created = await create_application.insert(
                CreateApplication(name=name, owner=owner, previous_team_id=previous_team_id, color=color)
            )

            row = await applications.get_by_pk(created.id)

            if row is None:
                # Written by the insert above on this connection - only a bug gets here.
                raise RuntimeError(f"Inserted application {created.id} could not be read back in the same transaction")

            return row

    async def get_team(self, team_id: int) -> TeamRow | None:
        """The team with *team_id*, or ``None`` if there's no such team."""
        return await self.team.get_by_pk(team_id)

    async def live_application_for(self, owner: int) -> ApplicationRow | None:
        """*owner*'s live application - pending or approved - or ``None``.

        One live application per owner is the schema's rule, so this answers
        both "already applied" and "already owns a team" with one read.
        """
        rows = self.application.from_records(
            await self.bot.database.fetch(
                "SELECT * FROM team_applications WHERE owner = $1 AND status IN ('pending', 'approved')", owner
            )
        )

        return rows[0] if rows else None

    async def role_for(self, team: TeamRow) -> discord.Role | None:
        """The team's Discord role, or ``None`` - one deleted by hand is unlinked, not repaired.

        The unlink only happens when Discord confirms the deletion; a fetch
        failure leaves the snowflake alone.
        """
        if team.role_snowflake is None:
            return None

        guild = self.bot.get_guild(self.bot.config.guild_id)

        if guild is None:
            logger.warning("Could not resolve role for team %d: guild %d not found", team.id, self.bot.config.guild_id)
            return None

        role = guild.get_role(team.role_snowflake)

        if role is None:
            try:
                role = await guild.fetch_role(team.role_snowflake)
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                logger.warning("Could not fetch role %d for team %d: %s", team.role_snowflake, team.id, e)
                return None

        if role is None:
            logger.info("Team %d role %d was deleted on Discord, unlinking", team.id, team.role_snowflake)
            await self.team.update(team, role_snowflake=None)
            return None

        if role.managed:
            logger.info("Team %d role %d is managed by another integration, unlinking", team.id, team.role_snowflake)
            await self.team.update(team, role_snowflake=None)
            return None

        return role

    async def emoji_for(self, team: TeamRow) -> discord.Emoji | None:
        """The team's Discord emoji, or ``None`` - one deleted by hand is unlinked, not repaired."""
        if team.emoji_snowflake is None:
            return None

        guild = self.bot.get_guild(self.bot.config.guild_id)

        if guild is None:
            logger.warning("Could not resolve emoji for team %d: guild %d not found", team.id, self.bot.config.guild_id)
            return None

        emoji = guild.get_emoji(team.emoji_snowflake)

        if emoji is None:
            try:
                emoji = await guild.fetch_emoji(team.emoji_snowflake)
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                logger.warning("Could not fetch emoji %d for team %d: %s", team.emoji_snowflake, team.id, e)
                return None

        if emoji is None:
            logger.info("Team %d emoji %d was deleted on Discord, unlinking", team.id, team.emoji_snowflake)
            await self.team.update(team, emoji_snowflake=None)
            return None

        return emoji

    async def previous_teams_for(self, player: int) -> list[TeamRow]:
        """The inactive teams *player* may link as history - ones they owned or coached.

        Feeds the ``previous_team`` autocomplete.
        """
        return self.team.from_records(
            await self.bot.database.fetch(
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
        )

    async def update_application(self, application: ApplicationRow, **fields: Any) -> ApplicationRow | None:
        """Partially update *application*'s row, or return ``None`` if the row is gone.

        The one update door into applications: ``logo_url`` and
        ``request_message_snowflake`` come through it, so commands stay on
        the service instead of reaching into its repository.
        """
        return await self.application.update(application, **fields)


async def validate_lineage(conn: DatabaseConnection, *, creator: int, previous_team_id: int) -> None:
    """Refuse a lineage link the rules don't allow.

    The previous team must be inactive with no successor yet, and *creator*
    must have owned or coached it.
    """
    teams = conn.get_repository(TeamRow)

    previous = teams.from_record(await conn.fetchrow("SELECT * FROM teams WHERE id = $1", previous_team_id))

    if previous is None:
        raise TeamError("That team doesn't exist.")

    if previous.status != "inactive":
        raise TeamError("You can only link the history of an inactive team.")

    successor = await conn.fetchval("SELECT 1 FROM teams WHERE previous_team_id = $1 LIMIT 1", previous_team_id)

    if successor is not None:
        raise TeamError("That team already has a successor.")

    eligible = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM transaction_log "
        "WHERE player = $1 AND role = 'coach' AND (from_team_id = $2 OR to_team_id = $2)) "
        "OR EXISTS (SELECT 1 FROM team_ownership WHERE player = $1 AND team_id = $2)",
        creator,
        previous_team_id,
    )

    if not eligible:
        raise TeamError("You must have owned or coached that team to claim its history.")
