import datetime

from ....core.database import Row

__all__ = ("ApplicationRow", "ApplicationTable", "CreateApplication")


class ApplicationTable(Row):
    """The ``team_applications`` table: the request that becomes a team, and its provenance after.

    The name and owner claims live here - held while the application is
    pending or approved, freed once it is denied or released.
    """

    class Meta:
        abstract = True
        table = "team_applications"
        primary_key = "id"
        generated = ("id", "created_at", "updated_at")


class ApplicationRow(ApplicationTable):
    """An application, whole.  What reads return."""

    id: int
    name: str
    owner: int
    logo_url: str | None
    request_message_snowflake: int | None
    previous_team_id: int | None
    status: str
    decided_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CreateApplication(ApplicationTable, kw_only=True):
    """An application, as an insert sends one - the pending request.

    Built with keywords, the inherited fields included.  ``id`` is defaulted
    so the insert's ``RETURNING *`` fills the key the database generated.
    """

    name: str
    owner: int
    previous_team_id: int | None = None
    id: int | None = None
