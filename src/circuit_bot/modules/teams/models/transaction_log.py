import datetime
from typing import Any

from ....core.database import Row

__all__ = ("CreateTransactionLog", "TransactionLogRow", "TransactionLogTable")


class TransactionLogTable(Row):
    """The ``transaction_log`` table: one append-only row per roster transaction.

    Nothing here is ever updated or deleted.  The ``roster`` view derives
    membership from it.  Ownership lives in ``team_ownership``, so ``role``
    is ``'coach'`` or ``NULL`` - held after ``promote``/``demote`` or before
    ``release``/``demand``.  ``details`` carries the offer id and status.
    """

    class Meta:
        abstract = True
        table = "transaction_log"
        primary_key = "id"
        generated = ("id", "at")


class TransactionLogRow(TransactionLogTable):
    """A transaction, whole.  What reads return."""

    id: int
    player: int
    from_team_id: int | None
    to_team_id: int | None
    action: str
    role: str | None
    actor: int | None
    details: dict[str, Any] | None
    at: datetime.datetime


class CreateTransactionLog(TransactionLogTable, kw_only=True):
    """A transaction, as an insert sends one.

    Built with keywords, the inherited fields included.  ``id`` is defaulted
    so the insert's ``RETURNING *`` fills the key the database generated.
    """

    player: int
    from_team_id: int | None = None
    to_team_id: int | None = None
    action: str
    role: str | None = None
    actor: int | None = None
    details: dict[str, Any] | None = None
    id: int | None = None
