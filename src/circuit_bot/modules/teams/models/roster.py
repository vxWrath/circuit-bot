from ....core.database import Row

__all__ = ("RosterRow",)


class RosterRow(Row):
    """One membership row in the ``roster`` view: a player's current role on a team.

    Read-only, with no single primary key - read it with
    ``find_by(team_id=...)``, never by primary key.  A plain player is
    ``role IS NULL``; a coach is ``role = 'coach'``; the owner is
    ``role = 'owner'``.
    """

    class Meta:
        table = "roster"

    team_id: int
    player: int
    role: str | None
