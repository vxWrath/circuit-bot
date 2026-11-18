import discord
from discord.app_commands import Choice, describe, rename
from discord.ext import commands

from ..core import (
    CircuitBot,
    CircuitGroup,
    Interaction,
    autocomplete,
    verified_only,
)


async def previous_teams_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    # Filled in once TeamService.previous_teams_for lands.
    return []


class Teams(commands.Cog):
    # Four entry points, one implementation.  The extra names answer "how do I
    # become a team owner?" from the angles a new member might try.
    register = CircuitGroup(name="register", description="Register for the league.", guild_only=True)
    create = CircuitGroup(name="create", description="Create something.", guild_only=True)
    start = CircuitGroup(name="start", description="Start something.", guild_only=True)

    apply = CircuitGroup(name="apply", description="Apply for something.", guild_only=True)
    apply_for = CircuitGroup(name="for", description="What you're applying for.", parent=apply)

    def __init__(self, bot: CircuitBot):
        self.bot = bot

        # Each call wraps the same function in its own Command, so the checks,
        # autocompletes, and body stay defined once.
        self.register.command(name="team", description="Register your team and apply to join the league.")(
            self.create_team
        )
        self.create.command(name="team", description="Alias for /register team")(self.create_team)
        self.start.command(name="team", description="Alias for /register team")(self.create_team)
        self.apply_for.command(name="owner", description="Alias for /register team")(self.create_team)

    @describe(
        name="The team name, 2 to 32 characters.",
        logo="A logo for your team. Will be converted to an emoji.",
        color="Hex color for your team's role, like #5865F2.",
        previous_team="An inactive team that you previously owned or coached, to carry on its history.",
    )
    @rename(previous_team="previous-team")
    @autocomplete(previous_team=previous_teams_autocomplete)
    @verified_only()
    async def create_team(
        self,
        interaction: Interaction,
        name: str,
        logo: discord.Attachment | None,
        color: str | None = None,
        previous_team: int | None = None,
    ):
        """The one create-team flow, run by every entry point below."""
        # Placeholder - the real body lands with TeamService.create_application.
        await interaction.failure("**Team applications aren't wired up yet.**")


async def setup(bot: CircuitBot):
    await bot.add_cog(Teams(bot))
