import datetime

from ....core.database import Row

__all__ = ("CreateOwnership", "OwnershipRow", "OwnershipTable")


class OwnershipTable(Row):
    """The ``team_ownership`` table: who owns a team, over time.

    The row with ``ended_at IS NULL`` is the current owner.  Transfer and
    removal close a row and open the next, and a team that leaves the
    league keeps its final owner.
    """

    class Meta:
        abstract = True
        table = "team_ownership"
        primary_key = "id"
        generated = ("id", "appointed_at")


class OwnershipRow(OwnershipTable):
    """An ownership, whole.  What reads return."""

    id: int
    team_id: int
    player: int
    appointed_at: datetime.datetime
    ended_at: datetime.datetime | None
    end_reason: str | None


class CreateOwnership(OwnershipTable, kw_only=True):
    """An ownership, as an insert sends one - the appointment at approval.

    Built with keywords, the inherited fields included.  ``id`` and
    ``appointed_at`` are defaulted so the insert's ``RETURNING *`` fills
    what the database generated.
    """

    team_id: int
    player: int
    appointed_at: datetime.datetime | None = None
    id: int | None = None
