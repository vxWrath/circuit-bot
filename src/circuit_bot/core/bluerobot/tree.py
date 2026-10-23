import datetime
from typing import TYPE_CHECKING

import discord
from discord.app_commands import CommandTree
from discord.app_commands.errors import CommandInvokeError, CommandOnCooldown

from ..api import APIRateLimitError
from ..bloxlink import BloxlinkError
from ..logging import get_logger
from ..roblox import RobloxError
from .app_commands import CommandNotFound
from .color import Color, CommonEmojis
from .errors import report_error
from .interaction import Interaction, InteractionOptions
from .layout import LayoutBuilder

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = ("CircuitTree",)

logger = get_logger("tree")

# API failures commands let propagate - each gets a reply naming the service.
API_ERRORS = (
    (BloxlinkError, "Bloxlink"),
    (RobloxError, "Roblox"),
)


class CircuitTree(CommandTree):
    client: "CircuitBot"

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
        # Cooldown checks raise pre-invoke, and commands let API failures
        # propagate, so both land here - a friendly reply instead of a traceback.
        text = self.format_error(interaction, error)

        if text is not None:
            try:
                await interaction.failure(text)
            except discord.HTTPException:
                pass

            return

        source = interaction.command.qualified_name if interaction.command else "interaction"

        await report_error(self.client, source, error, interaction=interaction)

    def format_error(self, interaction: Interaction, error: Exception) -> str | None:
        """The reply for an error a user can act on, ``None`` when it needs a traceback."""

        if isinstance(error, CommandInvokeError):
            error = error.original

        if isinstance(error, CommandOnCooldown):
            retry_at = discord.utils.utcnow() + datetime.timedelta(seconds=int(error.retry_after) + 1)
            retry = discord.utils.format_dt(retry_at, "R")

            fallback = f"{CommonEmojis.FAIL} **You're on cooldown. Try again {retry}.**"

            if interaction.command is None:
                return fallback

            try:
                command = self.client.retrieve_command(interaction.command.qualified_name)
            except CommandNotFound:
                return fallback

            return f"{CommonEmojis.FAIL} **{command.mention} is on cooldown. You may try again {retry}.**"

        for error_type, service in API_ERRORS:
            if isinstance(error, error_type):
                if isinstance(error, APIRateLimitError):
                    if error.retry_after is None:
                        retry = "later"
                    else:
                        retry_at = discord.utils.utcnow() + datetime.timedelta(seconds=int(error.retry_after) + 1)
                        retry = discord.utils.format_dt(retry_at, "R")

                    return f"{CommonEmojis.FAIL} **{service} is rate limited. Try again {retry}.**"

                return f"{CommonEmojis.FAIL} **{service} is unreachable right now. Try again later.**"

        return None
