from typing import TYPE_CHECKING, Any

import discord

if TYPE_CHECKING:
    from .bot import CircuitBot


class Interaction(discord.Interaction["CircuitBot"]):
    async def respond(
        self, **kwargs: Any
    ) -> discord.InteractionCallbackResponse["CircuitBot"] | discord.WebhookMessage:
        if self.response.is_done():
            return await self.followup.send(**kwargs)
        else:
            return await self.response.send_message(**kwargs)

    async def edit(
        self, **kwargs: Any
    ) -> discord.InteractionCallbackResponse["CircuitBot"] | discord.InteractionMessage | None:
        if self.response.is_done():
            return await self.edit_original_response(**kwargs)
        else:
            return await self.response.edit_message(**kwargs)
