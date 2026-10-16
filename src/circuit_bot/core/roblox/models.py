import msgspec

from ..model import APIModel, Model

__all__ = ("RobloxUser", "RobloxUserIngest")


class RobloxUserIngest(APIModel):
    """A Roblox user profile, as the users API spells it.

    The API spells two of these differently - ``username`` arrives as ``name``
    and ``display_name`` as ``displayName`` - so the wire spelling is carried
    here, on the field, and a response decodes straight into this shape.
    """

    id: int
    username: str = msgspec.field(name="name")
    display_name: str = msgspec.field(name="displayName")


class RobloxUser(Model):
    """A Roblox user profile, as the rest of the bot spells it.

    Built from :class:`RobloxUserIngest`, so what gets cached, compared and
    written back out is this spelling rather than the API's.
    """

    id: int
    username: str
    display_name: str
