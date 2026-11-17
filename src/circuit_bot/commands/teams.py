from discord.app_commands import Choice, describe
from discord.ext import commands

from ..core import CircuitBot, CircuitGroup, Interaction, autocomplete, verified_only


async def previous_teams_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    # Filled in once TeamService.previous_teams_for lands.
    return []


@describe(
    name="The team name, 2 to 32 characters.",
    color="Hex color for your team's role, like #5865F2.",
    previous_team="An inactive team you owned or coached, to carry on its history.",
)
@verified_only()
@autocomplete(previous_team=previous_teams_autocomplete)
async def create_team(
    interaction: Interaction,
    name: str,
    color: str | None = None,
    previous_team: int | None = None,
):
    """The one create-team flow, run by every entry point below."""
    # Placeholder - the real body lands with TeamService.create_application.
    await interaction.failure("**Team applications aren't wired up yet.**")


class Teams(commands.Cog):
    # Four entry points, one implementation.  The extra names answer "how do I
    # become a team owner?" from the angles a new member might try.
    create = CircuitGroup(name="create", description="Create a team.", guild_only=True)
    apply_for = CircuitGroup(name="apply-for", description="Apply for team ownership.", guild_only=True)
    register = CircuitGroup(name="register", description="Register for the league.", guild_only=True)
    start = CircuitGroup(name="start", description="Start a team.", guild_only=True)

    def __init__(self, bot: CircuitBot):
        self.bot = bot

        # Each call wraps the same function in its own Command, so the checks,
        # autocompletes, and body stay defined once.
        self.create.command(name="team", description="Create your own team and apply to join the league.")(create_team)
        self.apply_for.command(name="owner", description="Apply to become the owner of your own team.")(create_team)
        self.register.command(name="team", description="Register your team to join the league.")(create_team)
        self.start.command(name="team", description="Start your own team and apply to join the league.")(create_team)


async def setup(bot: CircuitBot):
    await bot.add_cog(Teams(bot))
