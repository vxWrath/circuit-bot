import discord
from discord.app_commands import Choice, describe, rename
from discord.ext import commands

from ..core import (
    CircuitBot,
    CircuitGroup,
    Color,
    CommonEmojis,
    DeferOptions,
    Interaction,
    InteractionOptions,
    LayoutBuilder,
    autocomplete,
    developer_only,
)

DEFER = {"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))}


async def guild_autocomplete(interaction: Interaction, current_value: str) -> list[Choice[str]]:
    if current_value.isdigit():
        guilds = [guild for guild in interaction.client.guilds if guild.id == int(current_value)]
    else:
        guilds = [guild for guild in interaction.client.guilds if guild.name.startswith(current_value)]

    return [Choice(name=f"{guild.name}: {guild.id}", value=str(guild.id)) for guild in guilds]


class Extensions(commands.Cog):
    def __init__(self, bot: CircuitBot):
        self.bot = bot

    ext = CircuitGroup(name="ext", description="Manage the bot's command and event extensions.", guild_only=True)

    @ext.command(name="reload", description="Reload every command extension.", extras=DEFER)
    @developer_only()
    async def reload(self, interaction: Interaction):
        await self.bot.load_commands_and_events()
        await interaction.success(f"{CommonEmojis.SUCCESS} **Reloaded command & event extensions**")

    @ext.command(name="unload", description="Unload every command extension.", extras=DEFER)
    @developer_only()
    async def unload(self, interaction: Interaction):
        await self.bot.unload_commands_and_events()
        await interaction.success(f"{CommonEmojis.SUCCESS} **Unloaded command & event extensions**")

    @ext.command(name="sync", description="Sync the bot's commands with Discord.", extras=DEFER)
    @describe(guild_id="The guild to use")
    @rename(guild_id="guild")
    @autocomplete(guild_id=guild_autocomplete)
    @developer_only()
    async def sync(self, interaction: Interaction, guild_id: str | None = None):
        guild: discord.Guild | None = None

        if guild_id is None:
            guild = interaction.require_guild()
        elif guild_id == "global":
            guild = discord.utils.MISSING
        elif guild_id.isdigit():
            guild = interaction.client.get_guild(int(guild_id))
        else:
            guild = next((g for g in interaction.client.guilds if g.name == guild_id), None)

        if guild is None:
            assert guild_id is not None

            return await interaction.failure(
                f"**Couldn't find guild with id `{guild_id}`**"
                if guild_id.isdigit()
                else f"**Couldn't find guild named `{guild_id}`**"
            )

        if guild is discord.utils.MISSING:
            text = "Synced global commands"
            app_commands = await self.bot.tree.sync()
        else:
            text = f"Synced commands for {guild.name}"
            self.bot.tree.copy_global_to(guild=guild)
            app_commands = await self.bot.tree.sync(guild=guild)

        self.bot.app_commands_cache = self.bot.fill_app_commands_cache(app_commands)
        self.bot.attach_cooldown_managers()

        await interaction.success(f"{CommonEmojis.SUCCESS} **{text} (**`{len(app_commands)}`** commands)**")


async def setup(bot: CircuitBot):
    await bot.add_cog(Extensions(bot))
