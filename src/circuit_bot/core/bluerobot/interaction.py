from typing import TYPE_CHECKING, Any

import discord

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = ("Interaction",)


class Interaction(discord.Interaction["CircuitBot"]):
    """The interaction the tree hands to a callback, with the bot's own methods.

    :meth:`~circuit_bot.core.bluerobot.tree.CircuitTree.interaction_check`
    relabels discord.py's object as this class - legal only while the two have
    the same layout, hence ``__slots__``; without it the relabelling raises
    ``TypeError``.  Nothing can be attached to one, so state an interaction
    carries goes in :attr:`discord.Interaction.extras`.
    """

    __slots__ = ()

    async def respond(
        self, **kwargs: Any
    ) -> discord.InteractionCallbackResponse["CircuitBot"] | discord.WebhookMessage:
        """Reply to the interaction, wherever the reply has to go.

        The first reply goes through the interaction itself and every one after
        it is a followup, so a caller never has to track which it is on.
        """

        if self.response.is_done():
            return await self.followup.send(**kwargs)
        else:
            return await self.response.send_message(**kwargs)

    async def edit(
        self, **kwargs: Any
    ) -> discord.InteractionCallbackResponse["CircuitBot"] | discord.InteractionMessage | None:
        """Edit the reply, whether or not it has been sent yet."""

        if self.response.is_done():
            return await self.edit_original_response(**kwargs)
        else:
            return await self.response.edit_message(**kwargs)
