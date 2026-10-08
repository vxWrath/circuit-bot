from typing import TYPE_CHECKING

import discord
from discord.app_commands import CommandTree

from .interaction import Interaction

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = ("CircuitTree",)


class CircuitTree(CommandTree):
    async def interaction_check(self, interaction: discord.Interaction["CircuitBot"]) -> bool:
        interaction.__class__ = Interaction
        
        # attach stuff to interaction here
        
        return True
