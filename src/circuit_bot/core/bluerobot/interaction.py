from typing import TYPE_CHECKING, Any

import discord

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = ("Interaction",)


class Interaction(discord.Interaction["CircuitBot"]):
    """The interaction the tree hands to a callback, with the bot's own methods.

    discord.py builds one plain :class:`discord.Interaction` per callback and
    gives a bot no say in the matter, so
    :meth:`~circuit_bot.core.bluerobot.tree.CircuitTree.interaction_check`
    relabels the object it was handed as this class instead.  That assignment
    is legal only while the two classes have the same layout, and
    :class:`discord.Interaction` is slotted - which is why this says
    ``__slots__`` too.  Leaving it off, or declaring a field of its own, puts a
    ``__dict__`` on the class and the assignment raises ``TypeError`` on the
    first command that arrives.

    Which is another way of saying nothing can be *attached* to one of these:
    there is nowhere to put it.  State an interaction carries goes in
    :attr:`discord.Interaction.extras`, the dict discord.py documents as the
    place for extraneous data and promises not to touch::

        interaction.extras["profile"] = players.profile(member.id)

    A property here is what keeps that typed, and is worth adding only when
    what it returns is core's - a module's shapes are the module's business.
    """

    __slots__ = ()

    async def respond(
        self, **kwargs: Any
    ) -> discord.InteractionCallbackResponse["CircuitBot"] | discord.WebhookMessage:
        """Reply to the interaction, wherever the reply has to go.

        The first reply goes through the interaction itself and every one after
        it is a followup, so a caller writes one call and never has to track
        which it is on.
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
