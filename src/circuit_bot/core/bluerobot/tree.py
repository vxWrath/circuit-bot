from typing import TYPE_CHECKING

import discord
from discord.app_commands import CommandTree

from .interaction import Interaction

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = ("CircuitTree",)


class CircuitTree(CommandTree):
    async def interaction_check(self, interaction: discord.Interaction["CircuitBot"]) -> bool:
        """Hand the callback an interaction of ours rather than discord.py's.

        Every command goes through here first, which is what makes it the place
        to relabel the interaction as :class:`~.interaction.Interaction`: the
        object discord.py built is ours for the rest of the callback.  The
        relabelling is only legal while the two classes have the same layout -
        see that class for why it is slotted.

        What a command needs *on* the interaction is attached here, in
        ``extras``, since that is the only place it can go::

            interaction.extras["profile"] = players.profile(interaction.user.id)
        """

        interaction.__class__ = Interaction

        return True
