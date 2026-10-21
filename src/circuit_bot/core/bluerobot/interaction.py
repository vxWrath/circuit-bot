import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar, Self, final

import discord
from discord.ui import (
    Button,
    DynamicItem,
    File,
    Item,
    MediaGallery,
    Section,
    Separator,
    TextDisplay,
    Thumbnail,
)
from discord.ui.dynamic import BaseT
from discord.ui.select import BaseSelect
from msgspec import field

from ..exception import CircuitException
from ..model import Model
from .color import Color

if TYPE_CHECKING:
    from .bot import CircuitBot
    from .cooldown import InteractionCooldowns
__all__ = (
    "BaseItem",
    "BaseLayoutView",
    "BaseView",
    "DeferOptions",
    "GuildOnly",
    "Interaction",
    "InteractionOptions",
    "MemberOnly",
    "UserOnly",
    "view_interaction_check",
)


class UserOnly(CircuitException):
    def __init__(self, *, interaction: bool = False):
        super().__init__(f"This {'interaction' if interaction else 'command'} can only be used by a user.")


class GuildOnly(CircuitException):
    def __init__(self, *, interaction: bool = False):
        super().__init__(f"This {'interaction' if interaction else 'command'} can only be used in a guild.")


class MemberOnly(CircuitException):
    def __init__(self, *, interaction: bool = False):
        super().__init__(f"This {'interaction' if interaction else 'command'} can only be used by a member.")


class DeferOptions(Model):
    defer: bool = field(default=False)
    ephemeral: bool = field(default=False)
    thinking: bool = field(default=False)

    def to_bits(self) -> int:
        return (self.defer << 0) | (self.ephemeral << 1) | (self.thinking << 2)


def shift_bit(value: int, position: int) -> bool:
    return bool((value >> position) & 1)


class InteractionOptions(Model):
    defer: DeferOptions = field(default_factory=DeferOptions)
    is_modal_response: bool = field(default=False)

    def to_custom_id(self) -> str:
        """Convert the options to a custom ID."""

        value = self.defer.to_bits()
        value |= self.is_modal_response << 3

        return f"{value:02x}"

    @classmethod
    def from_custom_id(cls, custom_id: str) -> Self:
        """Parse the custom ID into the options."""
        try:
            value = int(custom_id, 16)
        except ValueError, TypeError:
            return cls()

        defer = DeferOptions(
            defer=shift_bit(value, 0),
            ephemeral=shift_bit(value, 1),
            thinking=shift_bit(value, 2),
        )

        is_modal_response = shift_bit(value, 3)

        return cls(defer=defer, is_modal_response=is_modal_response)


class Interaction(discord.Interaction["CircuitBot"]):
    """The interaction the tree hands to a callback, with the bot's own methods.

    :meth:`~circuit_bot.core.bluerobot.tree.CircuitTree.interaction_check`
    relabels discord.py's object as this class - legal only while the two have
    the same layout, hence ``__slots__``; without it the relabelling raises
    ``TypeError``.  Nothing can be attached to one, so state an interaction
    carries goes in :attr:`discord.Interaction.extras`.
    """

    __slots__ = ()

    def get_options(self) -> InteractionOptions:
        """Get the interaction's options."""

        if self.command is not None:
            options = self.command.extras.get("options")
        else:
            options = self.extras.get("options")

        return options if options is not None else InteractionOptions()

    def require_guild(self) -> discord.Guild:
        if self.guild is None:
            raise GuildOnly(interaction=True)

        return self.guild

    def require_user(self) -> discord.User:
        if self.user is None or not isinstance(self.user, discord.User):
            raise UserOnly(interaction=True)

        return self.user

    def require_member(self) -> discord.Member:
        if self.user is None or not isinstance(self.user, discord.Member):
            raise MemberOnly(interaction=True)

        return self.user

    async def respond(
        self, **kwargs: Any
    ) -> discord.InteractionCallbackResponse["CircuitBot"] | discord.WebhookMessage:
        """Reply to the interaction, wherever the reply has to go.

        The first reply goes through the interaction itself and every one after
        it is a followup, so a caller never has to track which it is on.
        """

        if self.response.is_done():
            return await self.followup.send(**kwargs)
        else:
            return await self.response.send_message(**kwargs)

    async def edit(
        self, **kwargs: Any
    ) -> discord.InteractionCallbackResponse["CircuitBot"] | discord.InteractionMessage | None:
        """Edit the reply, whether or not it has been sent yet."""

        if self.response.is_done():
            return await self.edit_original_response(**kwargs)
        else:
            return await self.response.edit_message(**kwargs)

    async def success(self, text: str) -> None:
        """Reply with *text* in a green container, ephemeral."""

        # Imported here because layout.py imports this module's views, so a
        # module-level import would be circular.
        from .layout import LayoutBuilder

        layout = LayoutBuilder().container(accent_color=Color.brand_green()).text(text).done()
        await self.respond(view=layout, ephemeral=True)

    async def failure(self, text: str) -> None:
        """Reply with *text* in a red container, ephemeral."""

        # Imported here because layout.py imports this module's views, so a
        # module-level import would be circular.
        from .layout import LayoutBuilder

        layout = LayoutBuilder().container(accent_color=Color.brand_red()).text(text).done()
        await self.respond(view=layout, ephemeral=True)

    async def success_edit(self, text: str) -> None:
        """Edit the response with *text* in a green container"""

        # Imported here because layout.py imports this module's views, so a
        # module-level import would be circular.
        from .layout import LayoutBuilder

        layout = LayoutBuilder().container(accent_color=Color.brand_green()).text(text).done()
        await self.edit(view=layout)

    async def failure_edit(self, text: str) -> None:
        """Edit the response with *text* in a red container"""

        # Imported here because layout.py imports this module's views, so a
        # module-level import would be circular.
        from .layout import LayoutBuilder

        layout = LayoutBuilder().container(accent_color=Color.brand_red()).text(text).done()
        await self.edit(view=layout)

    @property
    def cooldowns(self) -> "InteractionCooldowns":
        """Named access to every cooldown on this command.

        Cooldowns are decorated with a name; the command body reaches each
        through ``interaction.cooldowns["<name>"]``.
        """
        if not self.command:
            raise ValueError("Interaction has no command")

        from .cooldown import InteractionCooldowns

        command_name = getattr(self.command, "qualified_name", self.command.name)
        return InteractionCooldowns(self, command_name)

    def cooldown_for(self, command_name: str) -> "InteractionCooldowns":
        """Named access to another command's cooldowns, to spend one here."""
        from .cooldown import InteractionCooldowns

        return InteractionCooldowns(self, command_name)


BASE_TEMPLATE = r"([0-9a-fA-F]{2})"


class BaseItem(ABC, DynamicItem[BaseT], template=BASE_TEMPLATE):
    checks: ClassVar[list[Callable[[Any, Interaction], bool]]] = []
    __original_template__: ClassVar[re.Pattern[str]] = re.compile(BASE_TEMPLATE)

    def __init__(self, item: BaseT, *, row: int | None = None, options: InteractionOptions | None = None) -> None:
        super().__init__(item=item, row=row)

        self.options = options or InteractionOptions()

        if isinstance(item, (Button, BaseSelect)):
            if not item.custom_id:
                raise ValueError("Dynamic items must have a custom_id set")

            item.custom_id = f"{self.options.to_custom_id()}:{item.custom_id}"

    def __init_subclass__(cls, *, template: str | re.Pattern[str]) -> None:
        cls.checks = []
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name, None)

            if attr is not None and callable(attr) and getattr(attr, "__is_check__", False):
                cls.checks.append(attr)  # type: ignore

        compiled_template = re.compile(template) if isinstance(template, str) else template

        # Strip leading ^ and trailing $ anchors so they don't break
        # when the pattern is concatenated with the options prefix.
        clean_pattern = compiled_template.pattern.lstrip("^").rstrip("$")
        cls.__original_template__ = re.compile(clean_pattern)

        return super().__init_subclass__(template=re.compile(BASE_TEMPLATE + ":" + clean_pattern))

    @property
    def template(self) -> re.Pattern[str]:
        """``re.Pattern``: The compiled regular expression that is used to parse the ``custom_id``."""
        return self.__class__.__original_template__

    @classmethod
    @final
    async def from_custom_id(
        cls: type[Self], interaction: Interaction, item: Item[Any], match: re.Match[str], /
    ) -> Self:
        group = match.group(1)
        options = InteractionOptions.from_custom_id(group)

        # Re-match without the options segment
        new_match = cls.__original_template__.match(match.string, len(group) + 1, match.end())

        if not new_match:
            # This should never happen
            raise ValueError("Failed to parse interaction after removing options segment")

        # Store options in extras so they can be accessed in interaction_check
        interaction.extras["options"] = options

        self = await cls.build(interaction, item, new_match)
        self.options = options

        return self

    @classmethod
    @abstractmethod
    async def build(cls: type[Self], interaction: Interaction, item: Item[Any], match: re.Match[str], /) -> Self:
        raise NotImplementedError(
            "Dynamic items must implement build() method to parse custom_id and return an instance"
        )

    def require_guild(self, interaction: Interaction) -> discord.Guild:
        if interaction.guild is None:
            raise GuildOnly(interaction=True)

        return interaction.guild

    def require_user(self, interaction: Interaction) -> discord.User:
        if interaction.user is None or not isinstance(interaction.user, discord.User):
            raise UserOnly(interaction=True)

        return interaction.user

    def require_member(self, interaction: Interaction) -> discord.Member:
        if interaction.user is None or not isinstance(interaction.user, discord.Member):
            raise MemberOnly(interaction=True)

        return interaction.user

    async def interaction_check(self, interaction: Interaction) -> bool:
        interaction.extras["options"] = self.options

        if not await interaction.client.tree.interaction_check(interaction):
            return False

        if interaction.channel and interaction.channel.type == discord.ChannelType.private:
            return True

        for check in self.checks:
            if not await discord.utils.maybe_coroutine(check, self, interaction):
                return False

        return True


class BaseView(discord.ui.View):
    children: list[DynamicItem[Button[Self] | BaseSelect[Self]]]

    async def on_error(self, interaction: Interaction, error: Exception, _: Item[Any]) -> None:
        return await interaction.client.tree.on_error(interaction, error)  # type: ignore


class BaseLayoutView(discord.ui.LayoutView):
    children: list[
        DynamicItem[Button[Self] | BaseSelect[Self]]
        | File[Self]
        | MediaGallery[Self]
        | Section[Self]
        | Separator[Self]
        | TextDisplay[Self]
        | Thumbnail[Self]
    ]

    async def on_error(self, interaction: Interaction, error: Exception, _: Item[Any]) -> None:
        return await interaction.client.tree.on_error(interaction, error)  # type: ignore


def view_interaction_check(func: Callable[[Any, Interaction], bool | Awaitable[bool]]):
    """Mark a method as a check for a BaseItem."""
    func.__is_check__ = True
    return func
