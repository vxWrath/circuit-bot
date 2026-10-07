from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("BloxlinkClient",)


class BloxlinkClient:
    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot
        self.session = bot.session
