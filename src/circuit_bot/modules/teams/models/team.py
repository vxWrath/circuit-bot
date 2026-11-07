import datetime

from ....core.database import Row

__all__ = ("CreateTeam", "TeamRow", "TeamTable")


class TeamTable(Row):
    """The ``teams`` table: one row per approved team, ever.

    Teams are only created at approval, so the Discord role always exists
    and the application it came from never changes.  A team that leaves the
    league is marked ``inactive``, never deleted, and a successor that
    carries its history links it with ``previous_team_id``.
    """

    class Meta:
        abstract = True
        table = "teams"
        primary_key = "id"
        generated = ("id", "approved_at", "created_at", "updated_at")


class TeamRow(TeamTable):
    """A team, whole.  What reads return."""

    id: int
    name: str
    application_id: int
    role_snowflake: int
    emoji_snowflake: int | None
    logo_url: str | None
    status: str
    approved_at: datetime.datetime
    inactivated_at: datetime.datetime | None
    previous_team_id: int | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CreateTeam(TeamTable, kw_only=True):
    """A team, as the approval's insert sends one.

    Built with keywords, the inherited fields included.  ``id`` and
    ``approved_at`` are defaulted so the insert's ``RETURNING *`` fills what
    the database generated.
    """

    name: str
    application_id: int
    role_snowflake: int
    emoji_snowflake: int | None = None
    logo_url: str | None = None
    previous_team_id: int | None = None
    approved_at: datetime.datetime | None = None
    id: int | None = None
