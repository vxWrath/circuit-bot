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
    get_logger,
    verified_only,
)
from ...modules import ApplicationRow, PlayerService, TeamRow, TeamService

logger = get_logger("teams_commands")

DEFER = {"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))}


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
            name="team", description="Register your team and apply to join the league.", extras=DEFER
        )(self.create_team)

        self.create.command(name="team", description="Alias for /register team", extras=DEFER)(self.create_team)
        self.start.command(name="team", description="Alias for /register team", extras=DEFER)(self.create_team)
        self.apply_for.command(name="owner", description="Alias for /register team", extras=DEFER)(self.create_team)

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
                f"{CommonEmojis.FAIL} **`{color}` isn't a hex color - use one like `#5865F2`.**"
            )

        if not 0 <= color_value <= 0xFFFFFF:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **Team colors must be between `#000000` and `#FFFFFF`.**"
            )

        team_service = self.bot.get_service(TeamService)

        # The pre-check names the reply precisely; the unique index still
        # owns the race between two simultaneous submissions.
        existing = await team_service.live_application_for(member.id)

        if existing is not None:
            if existing.status == "pending":
                return await interaction.failure(
                    f"{CommonEmojis.FAIL} **You already have a pending application with the team name `{existing.name}`.**"
                )

            return await interaction.failure(f"{CommonEmojis.FAIL} **You already own `{existing.name}`.**")

        player_service = self.bot.get_service(PlayerService)
        profile = player_service.profile(member)
        await profile.ensure_player()

        application = await team_service.register_application(
            name=name, owner=member.id, previous_team_id=previous_team, color=color_value
        )

        previous = (
            await team_service.get_team(application.previous_team_id)
            if application.previous_team_id is not None
            else None
        )

        # The request message and the logo are best-effort - a failed post
        # leaves the application in /pending teams without a queue handle.
        request_message: discord.Message | None = None

        try:
            channel = self.bot.get_channel(self.bot.config.league.pending_teams_channel_id)

            if isinstance(channel, discord.TextChannel):
                # A controlled filename, so the attachment:// reference in the
                # embed always matches what was uploaded.
                file = await logo.to_file(filename=f"logo{Path(logo.filename).suffix}") if logo is not None else None
                embed = self.request_embed(member, application, previous, file)

                if file is not None:
                    request_message = await channel.send(embed=embed, file=file)
                else:
                    request_message = await channel.send(embed=embed)

            else:
                logger.warning(
                    "Could not post application %d: channel %d not found",
                    application.id,
                    self.bot.config.league.pending_teams_channel_id,
                )
        except (discord.HTTPException, ValueError) as e:
            logger.warning("Could not post application %d: %s", application.id, e)

        if request_message is not None:
            await team_service.update_application(
                application,
                request_message_snowflake=request_message.id,
                logo_url=request_message.attachments[0].url if request_message.attachments else None,
            )

        color_hex = f"#{color_value:06X}"

        header = f"{CommonEmojis.SUCCESS} **Application for `{application.name}` sent**"
        details = f"**Role color:** `{color_hex}`"

        if previous is not None:
            details += f"\n**Previous team:** {previous.name}"

        next_steps = (
            "**What happens next**\n"
            "An approver will review your application and DM you with the decision. "
            "Approved teams get their Discord role and logo emoji. A denial comes with the reason, "
            "and you can apply again."
        )

        layout = LayoutBuilder().container(accent_color=Color(color_value))

        if logo is not None:
            layout.section().text(header).text(details).accessory(ui.Thumbnail(logo.url)).done()
            layout.separator()
            layout.text(next_steps)
        else:
            layout.text(header).text(details).separator().text(next_steps)

        await interaction.respond(view=layout.done(), ephemeral=True)

    def request_embed(
        self, member: discord.Member, application: ApplicationRow, previous: TeamRow | None, file: discord.File | None
    ) -> discord.Embed:
        """The pending-teams queue embed for *application*."""
        description = f"**Owner:** {member.mention}\n**Color:** `#{application.color:06X}`"

        if previous is not None:
            description += f"\n**Previous team:** {previous.name}"

        embed = discord.Embed(
            title=application.name,
            description=description,
            color=Color(application.color),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Use /approve team or /deny team to decide")

        if file is not None:
            # Referencing the uploaded attachment renders it as the thumbnail
            # and keeps it out of the message's attachment list.
            embed.set_thumbnail(url=f"attachment://{file.filename}")

        return embed


async def setup(bot: CircuitBot):
    await bot.add_cog(Teams(bot))
