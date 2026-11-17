"""The teams module's service: every read and write over applications, teams, their log, and their roster."""

from typing import TYPE_CHECKING

from ...core.service import Service
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


class TeamService(Service):
    """Team reads and writes over the database, the guild, and the Discord objects a team owns.

    Built up one method at a time as the commands land.
    """

    def __init__(self, *, bot: "CircuitBot") -> None:
        super().__init__(bot=bot)

        self.application = self.get_repository(ApplicationRow)
        self.team = self.get_repository(TeamRow)
        self.create_application = self.get_repository(CreateApplication)
        self.create_team = self.get_repository(CreateTeam)
        self.create_ownership = self.get_repository(CreateOwnership)
        self.create_log = self.get_repository(CreateTransactionLog)
        self.roster_view = self.get_repository(RosterRow)
