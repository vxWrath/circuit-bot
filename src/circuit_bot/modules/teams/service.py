"""The teams module's service: every read and write over applications, teams, their log, and their roster."""

import difflib
import re
from typing import TYPE_CHECKING, Any

import aiohttp
import discord

from ...core.bluerobot.utils import is_managed_role, normalize
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
from .profile import TeamProfile

if TYPE_CHECKING:
    from ...core.bluerobot import CircuitBot

__all__ = ("TeamService", "validate_team_color", "validate_team_name")

logger = get_logger("team_service")

# Two claimed names this alike read as the same team to a member.
SIMILARITY_THRESHOLD = 0.85

# Discord emoji names allow only letters, digits, and underscores.
EMOJI_NAME_PATTERN = re.compile(r"[^0-9A-Za-z_]+")


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

    Names are compared lowercase, whitespace-collapsed, and accent-folded, so
    accents or spacing alone never make two teams read differently.  Identical
    raw names return False - the unique index owns the exact-match reply, and
    its key is the raw lowercased name.
    """
    if new_name.lower() == claimed_name.lower():
        return False

    new = re.sub(r"\s+", " ", normalize(new_name).lower())
    claimed = re.sub(r"\s+", " ", normalize(claimed_name).lower())

    return difflib.SequenceMatcher(None, new, claimed).ratio() >= SIMILARITY_THRESHOLD


def emoji_name_for(team_name: str, application_id: int) -> str:
    """The guild emoji name for *team_name*, in Discord's letters-digits-underscore shape.

    Names are accent-folded so a team named Café gets Cafe rather than Caf_.
    A name with no ASCII left falls back to the application id, so the name
    is never empty, and the fallback matches on a retry.
    """
    name = EMOJI_NAME_PATTERN.sub("_", normalize(team_name))[:32]
    return name if len(name) >= 2 else f"team_{application_id}"


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

    def profile(self, team_id: int) -> TeamProfile:
        """Open *team_id*'s profile, touching nothing until a part is asked for."""
        return TeamProfile(self, team_id)

    async def get_team(self, team_id: int) -> TeamRow | None:
        """The team with *team_id*, or ``None`` if there's no such team."""
        return await self.team.get_by_pk(team_id)

    async def team_from_application(self, application_id: int) -> TeamRow | None:
        """The team approved from *application_id*, or ``None``."""
        teams = await self.team.find_by(application_id=application_id)
        return teams[0] if teams else None

    async def new_application(
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

    async def pending_applications(self) -> list[ApplicationRow]:
        """Every pending application, oldest first - the approver's queue."""
        return self.application.from_records(
            await self.bot.database.fetch(
                "SELECT * FROM team_applications WHERE status = 'pending' ORDER BY created_at, id"
            )
        )

    async def find_application_by_owner(self, owner: int) -> ApplicationRow | None:
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

    async def find_application_by_name(self, name: str) -> ApplicationRow | None:
        """The pending application named *name*, case-insensitively, or ``None``."""
        rows = self.application.from_records(
            await self.bot.database.fetch(
                "SELECT * FROM team_applications WHERE lower(name) = lower($1) AND status = 'pending'", name
            )
        )

        return rows[0] if rows else None

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

    async def approve(self, application: ApplicationRow) -> TeamRow | None:
        """Approve *application*: ensure its Discord role and emoji, then bring the team into existence.

        The role is required, so a failure to create it aborts the approval.
        The emoji is best-effort - no logo, or an expired upload, means no
        emoji.  ``None`` means a concurrent decision won.  A role or emoji
        reused from a crashed attempt stays for the retry, while objects this
        attempt created are deleted again on failure.
        """
        if application.status != "pending":
            raise TeamError("That application is no longer pending.")

        guild = self.bot.get_guild(self.bot.config.guild_id)

        if guild is None:
            raise TeamError("The league guild isn't available right now - try again later.")

        role, role_is_new = await self.ensure_role(guild, application)

        emoji: discord.Emoji | None = None
        emoji_is_new = False

        try:
            logo_url = await self.fresh_logo_url(application) or application.logo_url

            if logo_url is not None:
                emoji, emoji_is_new = await self.ensure_emoji(guild, application, logo_url)

            team = await self.approve_write(application, role, emoji, logo_url)
        except Exception:
            await self.cleanup_created(role, role_is_new, emoji, emoji_is_new)
            raise

        if team is None:
            await self.cleanup_created(role, role_is_new, emoji, emoji_is_new)

        return team

    async def ensure_role(self, guild: discord.Guild, application: ApplicationRow) -> tuple[discord.Role, bool]:
        """The application's role, created if the guild has none by that name yet.

        The second return says whether this call created it.  A crashed
        approval's leftover is found by name - team names are claim-locked,
        so a same-named role is that leftover, not a stranger's.
        """
        existing = discord.utils.get(guild.roles, name=application.name)

        if existing is not None:
            return existing, False

        return await guild.create_role(name=application.name, color=discord.Color(application.color)), True

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

    async def ensure_emoji(
        self, guild: discord.Guild, application: ApplicationRow, logo_url: str
    ) -> tuple[discord.Emoji | None, bool]:
        """The application's guild emoji, created from *logo_url* if none exists under its name.

        Emoji names are unique per guild, so a same-named one is the crashed
        attempt's leftover.  Creation is best-effort: a dead URL means no
        emoji and no error.
        """
        name = emoji_name_for(application.name, application.id)

        existing = discord.utils.get(guild.emojis, name=name)

        if existing is not None:
            return existing, False

        created = await self.create_emoji(guild, name, logo_url)
        return created, created is not None

    async def create_emoji(self, guild: discord.Guild, name: str, url: str) -> discord.Emoji | None:
        """Create the guild emoji *name* from the image at *url*, never raising.

        A failed download or create logs and returns ``None`` - the team is
        approved without an emoji.
        """
        try:
            async with self.bot.session.get(url) as response:
                response.raise_for_status()
                image = await response.read()

            return await guild.create_custom_emoji(name=name, image=image)
        except (aiohttp.ClientError, discord.HTTPException) as e:
            logger.warning("Could not create emoji for team %r: %s", name, e)
            return None

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
        concurrent decision won.
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
                    color=application.color,
                    previous_team_id=application.previous_team_id,
                )
            )

            team = await teams.get_by_pk(created.id)

            if team is None:
                # Written by the insert above on this connection - only a bug gets here.
                raise RuntimeError(f"Inserted team {created.id} could not be read back in the same transaction")

            await create_ownership.insert(CreateOwnership(team_id=team.id, player=application.owner))

            return team

    async def cleanup_created(
        self,
        role: discord.Role,
        role_is_new: bool,
        emoji: discord.Emoji | None,
        emoji_is_new: bool,
    ) -> None:
        """Take back down what this approval attempt created, never raising.

        A role or emoji reused from a crashed attempt predates this call and
        stays for the retry to reuse again.
        """
        if emoji_is_new and emoji is not None:
            await self.delete_emoji(emoji)

        if role_is_new:
            await self.delete_role(role)

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

    async def role_for(self, team: TeamRow) -> discord.Role | None:
        """The team's Discord role, or ``None`` - one deleted by hand or owned by Discord is unlinked, not repaired.

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

        if is_managed_role(role):
            logger.info("Team %d role %d is Discord-managed, unlinking", team.id, team.role_snowflake)
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
