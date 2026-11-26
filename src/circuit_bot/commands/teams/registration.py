"""The member-facing half of the team commands: the one registration flow and its aliases."""

from pathlib import Path

import discord
from discord import ui
from discord.app_commands import Choice, describe, rename
from discord.ext import commands

from ...core import (
    CircuitBot,
    CircuitGroup,
    Color,
    CommonEmojis,
    DeferOptions,
    Interaction,
    InteractionOptions,
    LayoutBuilder,
    autocomplete,
    create_logged_task,
    get_logger,
    verified_only,
)
from ...modules import ApplicationRow, PlayerService, TeamRow, TeamService
from .review import request_layout

logger = get_logger("teams_commands")


def parse_hex_color(raw: str) -> int:
    """*raw* as an RGB int - ``5865F2`` or ``#5865F2``.  Raises ``ValueError`` on garbage."""
    return int(raw.strip().lstrip("#"), 16)


async def previous_teams_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    current = current.lower()

    service = interaction.client.get_service(TeamService)
    teams = await service.previous_teams_for(interaction.user.id)

    return [Choice(name=team.name, value=str(team.id)) for team in teams if current in team.name.lower()][:25]


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
        self.register.command(
            name="team",
            description="Register your team and apply to join the league.",
            extras={"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))},
        )(self.create_team)

        self.create.command(
            name="team",
            description="Alias for /register team",
            extras={"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))},
        )(self.create_team)
        self.start.command(
            name="team",
            description="Alias for /register team",
            extras={"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))},
        )(self.create_team)
        self.apply_for.command(
            name="owner",
            description="Alias for /register team",
            extras={"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))},
        )(self.create_team)

    @describe(
        name="The team name, 2 to 32 characters.",
        color="Hex color for your team's role, like #5865F2.",
        logo="A logo for your team. Will be converted to an emoji.",
        previous_team="An inactive team that you previously owned or coached, to carry on its history.",
    )
    @rename(previous_team="previous-team")
    @autocomplete(previous_team=previous_teams_autocomplete)
    @verified_only()
    async def create_team(
        self,
        interaction: Interaction,
        name: str,
        color: str,
        logo: discord.Attachment | None = None,
        previous_team: int | None = None,
    ):
        """The one team-registration flow, run by every entry point."""
        member = interaction.require_member()

        try:
            color_value = parse_hex_color(color)
        except ValueError:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **`{color}` is not a hex color.**"
            )

        if not 0 <= color_value <= 0xFFFFFF:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **`{color}` is not a proper hex color.**"
            )

        team_service = self.bot.get_service(TeamService)

        # The pre-check names the reply precisely; the unique index still
        # owns the race between two simultaneous submissions.
        existing = await team_service.find_application_by_owner(member.id)

        if existing is not None:
            if existing.status == "pending":
                return await interaction.failure(
                    f"{CommonEmojis.FAIL} **You already have a pending application with the team name `{existing.name}`.**"
                )

            team = await team_service.team_from_application(existing.id)

            if team is not None:
                profile = team_service.profile(team.id)
                profile.set_part(TeamRow, team)

                mention = await profile.format_mention()
                return await interaction.failure(f"{CommonEmojis.FAIL} **You already own the {mention}.**")

            return await interaction.failure(f"{CommonEmojis.FAIL} **You already own the `{existing.name}`.**")

        player_service = self.bot.get_service(PlayerService)
        profile = player_service.profile(member)
        await profile.ensure_player()

        application = await team_service.new_application(
            name=name, owner=member.id, previous_team_id=previous_team, color=color_value
        )

        previous = (
            await team_service.get_team(application.previous_team_id)
            if application.previous_team_id is not None
            else None
        )

        # The card and the logo are follow-ups: the application is already
        # written, so a Discord failure here must not read as a failed
        # registration.  The task logs its own failure.
        create_logged_task(
            self.post_request_card(application, member.id, previous, logo),
            name=f"request-card-{application.id}",
        )

        details = (
            f"**Name:** `{application.name}`\n"
            f"**Color:** `{Color(color_value)}`\n"
            f"**Logo:** `{'see image on right' if logo is not None else 'no logo'}`\n"
        )

        if previous is not None:
            details += f"**Previous team:** `{previous.name}`\n"

        layout = LayoutBuilder().container(accent_color=Color(color_value))
        layout.text(f"## Application {application.id}\n")
        layout.separator()

        if logo is not None:
            section = layout.section().text(details)
            section.accessory(ui.Thumbnail(logo.url))
            section.done()
        else:
            layout.text(details)

        layout.separator()
        layout.text(
            "## What next?\n"
            "Your application is reviewed on the appropriateness of your team name and logo only. "
            "Your team's skill or competence play no part in the decision.\n\n"
            "An approver will review your application and DM you with the decision. "
            "A denial comes with the reason, and you can always apply again."
        )

        await interaction.respond(view=layout.done(), ephemeral=True)

    async def post_request_card(
        self,
        application: ApplicationRow,
        owner_snowflake: int,
        previous: TeamRow | None,
        logo: discord.Attachment | None,
    ) -> None:
        """Post *application*'s queue card and remember the message on the row.

        Runs detached from the command, so a missing channel or a failed
        send leaves the application without a queue handle instead of
        failing the registration that already wrote it.
        """
        channel = self.bot.get_channel(self.bot.config.league.pending_teams_channel_id)

        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Could not post application %d: channel %d not found",
                application.id,
                self.bot.config.league.pending_teams_channel_id,
            )
            return

        # A controlled filename, so a user-supplied name never leaks through.
        file = await logo.to_file(filename=f"logo{Path(logo.filename).suffix}") if logo is not None else None
        layout = request_layout(application, owner_snowflake, previous, logo.url if logo is not None else None)

        if file is None:
            request_message = await channel.send(view=layout)
        else:
            request_message = await channel.send(view=layout, file=file)

        await self.bot.get_service(TeamService).update_application(
            application,
            request_message_snowflake=request_message.id,
            logo_url=request_message.attachments[0].url if request_message.attachments else None,
        )


async def setup(bot: CircuitBot):
    await bot.add_cog(Teams(bot))
