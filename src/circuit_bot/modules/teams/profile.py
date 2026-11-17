from typing import TYPE_CHECKING

from ...core.profile import Profile

if TYPE_CHECKING:
    from .service import TeamService

__all__ = ("TeamProfile",)


class TeamProfile(Profile["TeamService"]):
    """One team, filled in on demand.

    Each part is fetched once and kept.
    """

    def __init__(self, service: "TeamService", team_id: int) -> None:
        super().__init__(service=service)

        self.team_id = team_id
