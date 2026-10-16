from typing import TYPE_CHECKING

import discord
from discord.app_commands import CommandTree

from .interaction import Interaction

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = ("CircuitTree",)


class CircuitTree(CommandTree):
    async def interaction_check(self, interaction: discord.Interaction["CircuitBot"]) -> bool:
        """Hand the callback one of our own interactions.

        Every command goes through here first, making it the place to relabel
        discord.py's object as :class:`~.interaction.Interaction` - legal only
        while the two have the same layout, see that class for why it is slotted.
        Anything a command needs *on* it goes in ``extras``.
        """

        interaction.__class__ = Interaction

        return True
