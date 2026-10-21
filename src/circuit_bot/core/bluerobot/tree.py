import io
import traceback

import discord
from discord.app_commands import CommandTree
from discord.app_commands.errors import CommandOnCooldown

from ..logging import get_logger
from .color import Color
from .interaction import Interaction, InteractionOptions
from .layout import LayoutBuilder

__all__ = ("CircuitTree",)

logger = get_logger("tree")


class CircuitTree(CommandTree):
    async def interaction_check(self, interaction: Interaction) -> bool:
        """Hand the callback one of our own interactions.

        Every command goes through here first, making it the place to relabel
        discord.py's object as :class:`~.interaction.Interaction` - legal only
        while the two have the same layout, see that class for why it is slotted.
        Anything a command needs *on* it goes in ``extras``.
        """

        # interaction is actually discord.Interaction[CircuitBot]
        interaction.__class__ = Interaction

        guild_id = interaction.guild.id if interaction.guild else "DM"
        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        logger.debug("Checking interaction %s in %s", command_name, guild_id)

        if interaction.type is discord.InteractionType.autocomplete:
            return True

        if not self.client.is_ready():
            layout = (
                LayoutBuilder()
                .container(accent_color=Color.brand_red())
                .text("**The bot is not ready yet. Please try again in a few moments.**")
                .done()
            )

            await interaction.respond(view=layout)
            return False

        if interaction.guild and interaction.guild.unavailable:
            try:
                layout = (
                    LayoutBuilder()
                    .container(accent_color=Color.brand_red())
                    .text("**This server is unavailable. This is a discord issue.**")
                    .done()
                )

                await interaction.respond(view=layout)
            except discord.HTTPException:
                pass

            return False

        if interaction.command:
            options: InteractionOptions = interaction.command.extras.get("options", InteractionOptions())
        else:
            options: InteractionOptions = interaction.extras.get("options", InteractionOptions())

        if not options.is_modal_response and options.defer.defer:
            await interaction.response.defer(ephemeral=options.defer.ephemeral, thinking=options.defer.thinking)

        return True

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        # Cooldown checks raise pre-invoke, so this is where a refused command
        # lands - a friendly reply instead of a traceback file.
        if isinstance(error, CommandOnCooldown):
            retry = f" in about {int(error.retry_after)}s" if error.retry_after is not None else ""
            layout = (
                LayoutBuilder()
                .container(accent_color=Color.brand_red())
                .text(f"**You're on cooldown. Try again{retry}.**")
                .done()
            )

            try:
                await interaction.respond(view=layout, ephemeral=True)
            except discord.HTTPException:
                pass

            return

        logger.error("Error in interaction", exc_info=error)

        trace = "".join(traceback.format_exception(error)).encode()
        file = discord.File(io.BytesIO(trace), filename="traceback.txt")

        try:
            await interaction.respond(file=file, ephemeral=True)
        except discord.HTTPException:
            pass
