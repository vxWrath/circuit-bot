from typing import TYPE_CHECKING

import discord

from ...core.database import RecordNotFoundError
from ...core.profile import Profile
from .errors import TeamWithoutRole
from .models import TeamRow

if TYPE_CHECKING:
    from .service import TeamService

__all__ = ("TeamProfile",)


class TeamProfile(Profile["TeamService"]):
    """One team, filled in on demand.

    Each part is fetched once and kept.  ``role`` and ``emoji`` self-heal: a
    Discord object deleted by hand or owned by Discord is unlinked from the
    row instead of repaired.
    """

    def __init__(self, service: "TeamService", team_id: int) -> None:
        super().__init__(service=service)

        self.team_id = team_id

    async def team(self) -> TeamRow | None:
        """The team's row, or ``None`` if there's no such team."""
        return await self.ensure_part(TeamRow, self.service.get_team, self.team_id)

    async def retrieve_team(self) -> TeamRow:
        """The team's row, or raise ``RecordNotFoundError``."""
        team = await self.team()

        if team is None:
            raise RecordNotFoundError("teams", "id", self.team_id)

        return team

    async def role(self) -> discord.Role:
        """The team's Discord role.  A team without one raises ``TeamWithoutRole``."""
        team = await self.retrieve_team()

        role = await self.ensure_part(discord.Role, self.service.role_for, team)

        if role is None:
            if team.role_snowflake is not None:
                # The service unlinked a role deleted on Discord - drop the stale row.
                self.invalidate_parts(TeamRow)
            raise TeamWithoutRole("That team has no Discord role.")

        return role

    async def emoji(self) -> discord.Emoji | None:
        """The team's Discord emoji, or ``None`` when there is none or it was unlinked."""
        team = await self.team()

        if team is None:
            return None

        emoji = await self.ensure_part(discord.Emoji, self.service.emoji_for, team)

        if emoji is None and team.emoji_snowflake is not None:
            self.invalidate_parts(TeamRow)

        return emoji

    async def format_name(self) -> str:
        """The team's display name, emoji first when there is one."""
        role = await self.role()
        emoji = await self.emoji()

        return f"{emoji} {role.name}" if emoji is not None else role.name

    async def format_mention(self) -> str:
        """The team's mention with its emoji."""
        role = await self.role()
        emoji = await self.emoji()

        return f"{emoji} {role.mention}" if emoji is not None else role.mention
