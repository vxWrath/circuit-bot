"""The approver-facing half of the team commands: the pending queue and its decisions."""

import discord
from discord.app_commands import Choice, describe
from discord.ext import commands

from ...core import (
    CircuitBot,
    CircuitGroup,
    Color,
    CommonEmojis,
    DeferOptions,
    Interaction,
    InteractionOptions,
    approver_only,
    autocomplete,
    create_logged_task,
    get_logger,
)
from ...modules import ApplicationRow, PlayerService, TeamRow, TeamService

logger = get_logger("teams_review_commands")

DEFER = {"options": InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))}


async def pending_names_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    current = current.lower()

    service = interaction.client.get_service(TeamService)
    applications = await service.pending()

    return [Choice(name=a.name, value=a.name) for a in applications if current in a.name.lower()][:25]


class TeamReview(commands.Cog):
    pending = CircuitGroup(name="pending", description="Pending things.", guild_only=True)
    approve = CircuitGroup(name="approve", description="Approve something.", guild_only=True)
    deny = CircuitGroup(name="deny", description="Deny something.", guild_only=True)

    def __init__(self, bot: CircuitBot):
        self.bot = bot

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
        create_logged_task(self.announce(team, application), name=f"announce-team-{team.id}")
        create_logged_task(self.dm_owner(team, application), name=f"dm-owner-{team.id}")
        create_logged_task(
            self.mark_decision(
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
        create_logged_task(self.dm_denial(application, reason), name=f"dm-denial-{application.id}")
        create_logged_task(
            self.mark_decision(application, interaction.user.display_name, decision="Denied"),
            name=f"mark-denial-{application.id}",
        )

        await interaction.success(f"**`{application.name}` denied.**")

    async def announce(self, team: TeamRow, application: ApplicationRow) -> None:
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

    async def dm_owner(self, team: TeamRow, application: ApplicationRow) -> None:
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

    async def dm_denial(self, application: ApplicationRow, reason: str | None) -> None:
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

    async def mark_decision(
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


async def setup(bot: CircuitBot):
    await bot.add_cog(TeamReview(bot))
