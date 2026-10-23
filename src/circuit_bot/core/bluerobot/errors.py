import io
import json
import traceback
from typing import TYPE_CHECKING

import aiohttp
import discord

from ..logging import get_logger
from .color import Color
from .interaction import Interaction

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = ("report_error",)

logger = get_logger("errors")


async def report_error(
    bot: "CircuitBot",
    source: str,
    error: Exception,
    *,
    interaction: Interaction | None = None,
) -> None:
    """Log *error*, post it to the error webhook, and reply to *interaction* with the traceback.

    The reply goes out first, so a slow webhook never delays it.
    """

    logger.error("Unhandled error in %s", source, exc_info=error)

    trace = "".join(traceback.format_exception(error))

    if interaction is not None:
        file = discord.File(io.BytesIO(trace.encode()), filename="traceback.txt")

        try:
            await interaction.respond(file=file, ephemeral=True)
        except discord.HTTPException:
            pass

    embed = discord.Embed(
        description=f"# Unhandled Error\n> **Source:** `{source}`\n```py\n{type(error).__name__}: {error}\n```",
        color=Color.brand_red(),
        timestamp=discord.utils.utcnow(),
    )

    form = aiohttp.FormData()
    form.add_field("payload_json", json.dumps({"embeds": [embed.to_dict()]}))
    form.add_field("files[0]", trace.encode(), filename="traceback.txt", content_type="text/plain")

    try:
        async with bot.session.post(bot.config.error_webhook_url, data=form) as resp:
            resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to send error webhook", exc_info=e)
