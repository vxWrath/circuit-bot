import msgspec

__all__ = ("RobloxUser",)


class RobloxUser(msgspec.Struct):
    """A Roblox user profile, as returned by the users API."""

    id: int
    username: str
    display_name: str
