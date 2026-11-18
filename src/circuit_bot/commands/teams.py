from pathlib import Path

import discord
from discord import ui
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
    approver_only,
    autocomplete,
    create_logged_task,
    get_logger,
    verified_only,
)
from ..modules import ApplicationRow, PlayerService, TeamRow, TeamService

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


async def pending_names_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    current = current.lower()

    service = interaction.client.get_service(TeamService)
    applications = await service.pending()

    return [Choice(name=a.name, value=a.name) for a in applications if current in a.name.lower()][:25]


class Teams(commands.Cog):
    # Four entry points, one implementation.  The extra names answer "how do I
    # become a team owner?" from the angles a new member might try.
    register = CircuitGroup(name="register", description="Register for the league.", guild_only=True)
    create = CircuitGroup(name="create", description="Create something.", guild_only=True)
    start = CircuitGroup(name="start", description="Start something.", guild_only=True)
    pending = CircuitGroup(name="pending", description="Pending things.", guild_only=True)
    approve = CircuitGroup(name="approve", description="Approve something.", guild_only=True)
    deny = CircuitGroup(name="deny", description="Deny something.", guild_only=True)

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

    @pending.command(name="teams", description="List every pending team application.", extras=DEFER)
    @approver_only()
    async def pending_teams(self, interaction: Interaction):
        """The approver's queue: every pending application, oldest first."""
        team_service = self.bot.get_service(TeamService)
        applications = await team_service.pending()

        if not applications:
            return await interaction.success(
                f"{CommonEmojis.SUCCESS} **The queue is clear - no pending team applications.**"
            )

        # One lookup per distinct predecessor, so a shared history costs nothing extra.
        previous_teams: dict[int, TeamRow | None] = {}

        for application in applications:
            if application.previous_team_id is not None and application.previous_team_id not in previous_teams:
                previous_teams[application.previous_team_id] = await team_service.get_team(application.previous_team_id)

        await interaction.respond(embed=self.queue_embed(applications, previous_teams), ephemeral=True)

    def queue_embed(
        self, applications: list[ApplicationRow], previous_teams: dict[int, TeamRow | None]
    ) -> discord.Embed:
        """The queue embed for /pending teams: every pending application in one view."""
        embed = discord.Embed(
            title=f"{len(applications)} pending team application{'' if len(applications) == 1 else 's'}",
            color=Color.circuit_blue(),
            timestamp=discord.utils.utcnow(),
        )

        # An embed holds at most 25 fields, so the oldest come first and the
        # channel itself holds the rest.
        for application in applications[:25]:
            value = (
                f"<@{application.owner}>\n"
                f"Color `#{application.color:06X}` · submitted "
                f"{discord.utils.format_dt(application.created_at, 'R')}"
            )

            if application.request_message_snowflake is not None:
                url = (
                    f"https://discord.com/channels/{self.bot.config.guild_id}/"
                    f"{self.bot.config.league.pending_teams_channel_id}/{application.request_message_snowflake}"
                )
                value += f"\n[Request]({url})"

            if application.previous_team_id is not None:
                previous = previous_teams.get(application.previous_team_id)

                if previous is not None:
                    value += f"\nContinuing **{previous.name}**"

            embed.add_field(name=application.name, value=value, inline=True)

        if len(applications) > 25:
            embed.set_footer(text=f"{len(applications) - 25} more pending in the pending-teams channel")

        return embed

    @approve.command(name="team", description="Approve a pending team application.", extras=DEFER)
    @describe(name="The pending application's team name.")
    @autocomplete(name=pending_names_autocomplete)
    @approver_only()
    async def approve_team(self, interaction: Interaction, name: str):
        """Approve a pending application: the team, its role, and its logo come into existence."""
        team_service = self.bot.get_service(TeamService)

        application = await team_service.find_application_by_name(name)

        if application is None:
            return await interaction.failure(f"{CommonEmojis.FAIL} **No pending application named `{name}`.**")

        team = await team_service.approve(application)

        if team is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **`{application.name}` was already decided by another approver.**"
            )

        # The follow-ups run in the background, so a closed DM or a deleted
        # message can't undo the approval.
        create_logged_task(self._announce(team, application), name=f"announce-team-{team.id}")
        create_logged_task(self._dm_owner(team, application), name=f"dm-owner-{team.id}")
        create_logged_task(
            self._mark_decision(
                application, interaction.user.display_name, decision="Approved", footer_note=f"team #{team.id}"
            ),
            name=f"mark-request-{team.id}",
        )

        await interaction.success(
            f"{CommonEmojis.SUCCESS} **`{application.name}` approved - team #{team.id} is live.**"
        )

    @deny.command(name="team", description="Deny a pending team application.", extras=DEFER)
    @describe(
        name="The pending application's team name.",
        reason="Why the application is being denied. Sent to the owner.",
    )
    @autocomplete(name=pending_names_autocomplete)
    @approver_only()
    async def deny_team(self, interaction: Interaction, name: str, reason: str | None = None):
        """Deny a pending application, freeing the name and the owner's slot."""
        team_service = self.bot.get_service(TeamService)

        application = await team_service.find_application_by_name(name)

        if application is None:
            return await interaction.failure(f"{CommonEmojis.FAIL} **No pending application named `{name}`.**")

        decided = await team_service.deny(application)

        if decided is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **`{application.name}` was already decided by another approver.**"
            )

        # The follow-ups run in the background, so a closed DM or a deleted
        # message can't undo the denial.
        create_logged_task(self._dm_denial(application, reason), name=f"dm-denial-{application.id}")
        create_logged_task(
            self._mark_decision(application, interaction.user.display_name, decision="Denied"),
            name=f"mark-denial-{application.id}",
        )

        await interaction.success(f"**`{application.name}` denied.**")

    async def _announce(self, team: TeamRow, application: ApplicationRow) -> None:
        """Post the approval announcement to the new-teams channel - the public record."""
        channel = self.bot.get_channel(self.bot.config.league.new_teams_channel_id)

        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Could not announce team %d: channel %d not found",
                team.id,
                self.bot.config.league.new_teams_channel_id,
            )
            return

        emoji = (
            discord.utils.get(channel.guild.emojis, id=team.emoji_snowflake)
            if team.emoji_snowflake is not None
            else None
        )

        embed = discord.Embed(
            title=f"{emoji} {team.name}" if emoji is not None else team.name,
            description=f"**Owner:** <@{application.owner}>\n**Color:** `#{application.color:06X}`",
            color=Color(application.color),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Team #{team.id}")

        if team.logo_url is not None:
            embed.set_thumbnail(url=team.logo_url)

        await channel.send(embed=embed)

    async def _dm_owner(self, team: TeamRow, application: ApplicationRow) -> None:
        """DM the new owner with the decision and what they gained."""
        player_service = self.bot.get_service(PlayerService)
        owner = await player_service.member(snowflake=application.owner)

        if owner is None:
            logger.warning("Could not DM owner %d of team %d: not in guild", application.owner, team.id)
            return

        description = f"**{team.name}** is now a league team!"

        if team.role_snowflake is not None:
            description += f"\nYour role: <@&{team.role_snowflake}>"

        description += f"\nYour color: `#{application.color:06X}`"

        embed = discord.Embed(
            title="Application approved",
            description=description,
            color=Color.brand_green(),
        )

        await owner.send(embed=embed)

    async def _dm_denial(self, application: ApplicationRow, reason: str | None) -> None:
        """DM the owner that their application was denied, with the reason."""
        player_service = self.bot.get_service(PlayerService)
        owner = await player_service.member(snowflake=application.owner)

        if owner is None:
            logger.warning("Could not DM owner %d about denial: not in guild", application.owner)
            return

        description = f"**{application.name}** was denied."

        if reason:
            description += f"\n**Reason:** {reason}"

        description += "\nYou can apply again with the same or a new name."

        embed = discord.Embed(
            title="Application denied",
            description=description,
            color=Color.brand_red(),
        )

        await owner.send(embed=embed)

    async def _mark_decision(
        self, application: ApplicationRow, approver: str, *, decision: str, footer_note: str = ""
    ) -> None:
        """Update the request embed in the pending-teams channel with the decision.

        *decision* is "Approved" or "Denied" - the color and check mark
        follow it, and the footer names the approver.
        """
        if application.request_message_snowflake is None:
            return

        channel = self.bot.get_channel(self.bot.config.league.pending_teams_channel_id)

        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Could not update request embed for application %d: channel %d not found",
                application.id,
                self.bot.config.league.pending_teams_channel_id,
            )
            return

        try:
            message = await channel.fetch_message(application.request_message_snowflake)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.warning("Could not fetch request message for application %d: %s", application.id, e)
            return

        embed = message.embeds[0] if message.embeds else None

        if embed is None:
            return

        if decision == "Approved":
            prefix, color = CommonEmojis.SUCCESS, Color.brand_green()
        else:
            prefix, color = CommonEmojis.FAIL, Color.brand_red()

        footer = f"{decision} by {approver}"

        if footer_note:
            footer += f" · {footer_note}"

        embed.title = f"{prefix} {embed.title}"
        embed.color = color
        embed.set_footer(text=footer)

        try:
            await message.edit(embed=embed)
        except discord.HTTPException as e:
            logger.warning("Could not update request embed for application %d: %s", application.id, e)

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
