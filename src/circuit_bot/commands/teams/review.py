"""The approver-facing half of the team commands: the pending queue and its decisions."""

import re
from typing import Self

import discord
from discord import ui
from discord.app_commands import Choice, describe
from discord.ext import commands

from ...core import (
    BaseItem,
    BaseModal,
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
    ensure_approver,
    get_logger,
    view_interaction_check,
)
from ...modules import ApplicationRow, PlayerService, TeamRow, TeamService

logger = get_logger("teams_review_commands")

DEFER_OPTIONS = InteractionOptions(defer=DeferOptions(defer=True, ephemeral=True))
DEFER = {"options": DEFER_OPTIONS}

DECISION_HINT = "-# Use the buttons below to decide, or use /approve team or /deny team"


async def pending_names_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    current = current.lower()

    service = interaction.client.get_service(TeamService)
    applications = await service.pending_applications()

    return [Choice(name=a.name, value=a.name) for a in applications if current in a.name.lower()][:25]


def request_layout(
    application: ApplicationRow,
    owner_snowflake: int,
    previous: TeamRow | None,
    logo_url: str | None,
    *,
    accent: Color | None = None,
    decision_line: str | None = None,
) -> discord.ui.LayoutView:
    """The pending-teams queue card for *application*.

    *decision_line* renders the decided state: the accent and the last
    block follow it.  Without it the card shows the pending hint and the
    decision buttons.
    """
    details = (
        f"**Name:** `{application.name}`\n"
        f"**Owner:** <@{owner_snowflake}> • `{owner_snowflake}`\n"
        f"**Color:** `#{application.color:06X}`\n"
        f"**Logo:** `{'see image on right' if logo_url is not None else 'no logo'}`\n"
    )

    if previous is not None:
        details += f"**Previous team:** {previous.name}\n"

    layout = LayoutBuilder().container(accent_color=accent or Color(application.color))
    layout.text(f"## Application {application.id}")
    layout.separator()

    if logo_url is not None:
        section = layout.section().text(details)
        section.accessory(ui.Thumbnail(logo_url))
        section.done()
    else:
        layout.text(details)

    layout.separator()
    layout.text(decision_line or DECISION_HINT)

    if decision_line is None:
        # A decided card drops the buttons, so a press on a stale copy of
        # the pending card just hits an already-decided application.
        layout.action_row().add(ApproveTeamButton(application.id)).add(DenyTeamButton(application.id)).done()

    return layout.done()


def decision_layout(
    application: ApplicationRow,
    approver_id: int,
    *,
    team: TeamRow | None = None,
    reason: str | None = None,
    emoji: discord.Emoji | None = None,
) -> discord.ui.LayoutView:
    """The approver's reply for a decision: the new team on approve, the reason on deny.

    The title and the closing line echo the decided card: the application
    id up top, the approver below.
    """
    if team is not None:
        title = f"# Approved Application {application.id}"
        accent = Color.brand_green()
        details = (
            f"**Name:** `{application.name}`\n"
            f"**Team ID:** `{team.id}`\n"
        )

        if team.role_snowflake is not None:
            details += f"**Role:** <@&{team.role_snowflake}>\n"

        details += f"**Emoji:** {emoji if emoji is not None else '`no emoji`'}\n"
        details += f"**Owner:** <@{application.owner}>\n"

        line = f"{CommonEmojis.SUCCESS} Approved by <@{approver_id}> • `{approver_id}`"
        logo_url = team.logo_url
    else:
        title = f"# Denied Application {application.id}"
        accent = Color.brand_red()
        details = (
            f"**Name:** `{application.name}`\n"
            f"**Owner:** <@{application.owner}>\n"
            f"**Color:** `#{application.color:06X}`\n"
        )

        if reason:
            details += f"**Reason:** {reason}\n"

        line = f"{CommonEmojis.FAIL} Denied by <@{approver_id}> • `{approver_id}`"
        logo_url = None

    layout = LayoutBuilder().container(accent_color=accent)
    layout.text(title)
    layout.separator()

    if logo_url is not None:
        section = layout.section().text(details)
        section.accessory(ui.Thumbnail(logo_url))
        section.done()
    else:
        layout.text(details)

    layout.separator()
    layout.text(f"-# {line}")

    return layout.done()


class DecisionCheck:
    """The approver check shared by the card's decision buttons."""

    @view_interaction_check
    async def approver_check(self, interaction: Interaction) -> bool:
        ensure_approver(interaction)
        return True


class ApproveTeamButton(DecisionCheck, BaseItem[ui.Button], template=r"team:approve:(\d+)"):
    """The card's approve button: run the approval and mark the card."""

    def __init__(self, application_id: int):
        button = ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"team:approve:{application_id}",
        )
        # Approval creates roles and emojis, so the reply is deferred.
        super().__init__(button, options=DEFER_OPTIONS)
        self.application_id = application_id

    @classmethod
    async def build(cls, interaction: Interaction, item: ui.Item, match: re.Match[str]) -> Self:
        return cls(int(match.group(1)))

    async def callback(self, interaction: Interaction) -> None:
        application = await interaction.client.get_service(TeamService).find_application_by_id(self.application_id)

        if application is None:
            return await interaction.failure(f"{CommonEmojis.FAIL} **This application was already decided.**")

        team = await run_approval(interaction.client, application, interaction.user.display_name)

        if team is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **`{application.name}` was already decided by another approver.**"
            )

        await interaction.respond(
            view=decision_layout(
                application,
                interaction.user.id,
                team=team,
                emoji=guild_emoji_for(interaction.client, team),
            ),
            ephemeral=True,
        )


class DenyTeamButton(DecisionCheck, BaseItem[ui.Button], template=r"team:deny:(\d+)"):
    """The card's deny button: ask for a reason, then run the denial."""

    def __init__(self, application_id: int):
        button = ui.Button(
            label="Deny",
            style=discord.ButtonStyle.danger,
            custom_id=f"team:deny:{application_id}",
        )
        super().__init__(button, options=InteractionOptions(is_modal_response=True))
        self.application_id = application_id

    @classmethod
    async def build(cls, interaction: Interaction, item: ui.Item, match: re.Match[str]) -> Self:
        return cls(int(match.group(1)))

    async def callback(self, interaction: Interaction) -> None:
        await interaction.response.send_modal(DenyReasonModal(self.application_id))


class DenyReasonModal(BaseModal, title="Deny application"):
    modal_source = "team-denial"

    reason = ui.TextInput(
        label="Reason (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
        placeholder="Why the application is being denied",
    )

    def __init__(self, application_id: int):
        super().__init__()
        self.application_id = application_id

    async def handle_submit(self, interaction: Interaction) -> None:
        application = await interaction.client.get_service(TeamService).find_application_by_id(self.application_id)

        if application is None:
            return await interaction.failure(f"{CommonEmojis.FAIL} **This application was already decided.**")

        reason = self.reason.value.strip() or None
        decided = await run_denial(interaction.client, application, interaction.user.display_name, reason)

        if decided is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **`{application.name}` was already decided by another approver.**"
            )

        await interaction.respond(view=decision_layout(application, interaction.user.id, reason=reason), ephemeral=True)


async def run_approval(bot: CircuitBot, application: ApplicationRow, approver: str) -> TeamRow | None:
    """Approve *application* and launch the follow-ups.  ``None`` means a concurrent decision won.

    The follow-ups run in the background, so a closed DM or a deleted
    message can't undo the approval.
    """
    team = await bot.get_service(TeamService).approve(application)

    if team is None:
        return None

    create_logged_task(announce(bot, team, application), name=f"announce-team-{team.id}")
    create_logged_task(dm_owner(bot, team, application), name=f"dm-owner-{team.id}")
    create_logged_task(
        mark_decision(bot, application, approver, decision="Approved", footer_note=f"team #{team.id}"),
        name=f"mark-request-{team.id}",
    )

    return team


async def run_denial(
    bot: CircuitBot, application: ApplicationRow, approver: str, reason: str | None
) -> ApplicationRow | None:
    """Deny *application* and launch the follow-ups.  ``None`` means a concurrent decision won.

    The follow-ups run in the background, so a closed DM or a deleted
    message can't undo the denial.
    """
    decided = await bot.get_service(TeamService).deny(application)

    if decided is None:
        return None

    create_logged_task(dm_denial(bot, application, reason), name=f"dm-denial-{application.id}")
    create_logged_task(
        mark_decision(bot, application, approver, decision="Denied"),
        name=f"mark-denial-{application.id}",
    )

    return decided


def guild_emoji_for(bot: CircuitBot, team: TeamRow) -> discord.Emoji | None:
    """The team's guild emoji, resolved for display, or ``None`` when it has none or the guild is gone."""
    if team.emoji_snowflake is None:
        return None

    guild = bot.get_guild(bot.config.guild_id)

    if guild is None:
        return None

    return discord.utils.get(guild.emojis, id=team.emoji_snowflake)


async def announce(bot: CircuitBot, team: TeamRow, application: ApplicationRow) -> None:
    """Post the approval announcement to the new-teams channel - the public record."""
    channel = bot.get_channel(bot.config.league.new_teams_channel_id)

    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "Could not announce team %d: channel %d not found",
            team.id,
            bot.config.league.new_teams_channel_id,
        )
        return

    emoji = guild_emoji_for(bot, team)

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


async def dm_owner(bot: CircuitBot, team: TeamRow, application: ApplicationRow) -> None:
    """DM the new owner with the decision and what they gained."""
    owner = await bot.get_service(PlayerService).member(snowflake=application.owner)

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


async def dm_denial(bot: CircuitBot, application: ApplicationRow, reason: str | None) -> None:
    """DM the owner that their application was denied, with the reason."""
    owner = await bot.get_service(PlayerService).member(snowflake=application.owner)

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
    bot: CircuitBot, application: ApplicationRow, approver: str, *, decision: str, footer_note: str = ""
) -> None:
    """Update the request card in the pending-teams channel with the decision.

    *decision* is "Approved" or "Denied".  The card is a layout, so it
    is rebuilt from the application row with the decision accent and
    line instead of being mutated in place.
    """
    if application.request_message_snowflake is None:
        return

    channel = bot.get_channel(bot.config.league.pending_teams_channel_id)

    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "Could not update request card for application %d: channel %d not found",
            application.id,
            bot.config.league.pending_teams_channel_id,
        )
        return

    try:
        message = await channel.fetch_message(application.request_message_snowflake)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        logger.warning("Could not fetch request message for application %d: %s", application.id, e)
        return

    if decision == "Approved":
        prefix, color = CommonEmojis.SUCCESS, Color.brand_green()
    else:
        prefix, color = CommonEmojis.FAIL, Color.brand_red()

    line = f"{decision} by {approver}"

    if footer_note:
        line += f" • {footer_note}"

    previous = (
        await bot.get_service(TeamService).get_team(application.previous_team_id)
        if application.previous_team_id is not None
        else None
    )

    layout = request_layout(
        application,
        application.owner,
        previous,
        application.logo_url,
        accent=color,
        decision_line=f"-# {prefix} {line}",
    )

    try:
        await message.edit(view=layout)
    except discord.HTTPException as e:
        logger.warning("Could not update request card for application %d: %s", application.id, e)


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
        applications = await team_service.pending_applications()

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
                f"Color `#{application.color:06X}` • submitted "
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

        team = await run_approval(self.bot, application, interaction.user.display_name)

        if team is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **`{application.name}` was already decided by another approver.**"
            )

        await interaction.respond(
            view=decision_layout(
                application,
                interaction.user.id,
                team=team,
                emoji=guild_emoji_for(self.bot, team),
            ),
            ephemeral=True,
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

        decided = await run_denial(self.bot, application, interaction.user.display_name, reason)

        if decided is None:
            return await interaction.failure(
                f"{CommonEmojis.FAIL} **`{application.name}` was already decided by another approver.**"
            )

        await interaction.respond(view=decision_layout(application, interaction.user.id, reason=reason), ephemeral=True)


async def setup(bot: CircuitBot):
    await bot.add_cog(TeamReview(bot))
