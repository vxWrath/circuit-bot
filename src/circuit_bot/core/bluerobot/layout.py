from typing import Self

import discord
from discord import MediaGalleryItem, SeparatorSpacing, UnfurledMediaItem, ui

from .interaction import BaseLayoutView

__all__ = [
    "LayoutBuilder",
]

type MediaSource = str | discord.File | UnfurledMediaItem


# ---------------------------------------------------------------------------
# Shared mixins
# ---------------------------------------------------------------------------


class ContentMixin:
    """Content component helpers (text, separator, gallery, file)."""

    def add(self, item: ui.Item) -> None:
        raise NotImplementedError

    def text(self, content: str) -> Self:
        self.add(ui.TextDisplay(content))
        return self

    def separator(
        self,
        *,
        visible: bool = True,
        spacing: SeparatorSpacing = SeparatorSpacing.small,
    ) -> Self:
        self.add(ui.Separator(visible=visible, spacing=spacing))
        return self

    def gallery(
        self,
        *items: MediaSource | MediaGalleryItem,
        spoiler: bool = False,
    ) -> Self:
        gallery_items: list[MediaGalleryItem] = [
            item if isinstance(item, MediaGalleryItem) else MediaGalleryItem(item, spoiler=spoiler) for item in items
        ]
        self.add(ui.MediaGallery(*gallery_items))
        return self

    def file(self, url: MediaSource, *, spoiler: bool = False) -> Self:
        self.add(ui.File(url, spoiler=spoiler))
        return self


class LayoutMixin(ContentMixin):
    """Extends content mixin with nested layout builders (action_row, section, container)."""

    def action_row(self) -> "ActionRowBuilder[Self]":
        return ActionRowBuilder(self)

    def section(self) -> "SectionBuilder[Self]":
        return SectionBuilder(self)

    def container(
        self,
        *,
        accent_color: discord.Color | int | None = None,
        spoiler: bool = False,
    ) -> "ContainerBuilder[Self]":
        return ContainerBuilder(self, accent_color=accent_color, spoiler=spoiler)


# ---------------------------------------------------------------------------
# Nested builders
# ---------------------------------------------------------------------------


class ActionRowBuilder[ParentT: LayoutMixin]:
    """Nested builder for :class:`discord.ui.ActionRow`.

    Add up to 5 buttons or a single select via :meth:`add`, then call
    :meth:`done` to add the row to the parent and return it.

    Example::

        layout.action_row().add(accept_btn).add(decline_btn).done()
    """

    def __init__(self, parent: ParentT) -> None:
        self.parent = parent
        self.items: list[ui.Item] = []

    def add(self, item: ui.Item) -> Self:
        self.items.append(item)
        return self

    def done(self) -> ParentT:
        self.parent.add(ui.ActionRow(*self.items))
        return self.parent


class SectionBuilder[ParentT: LayoutMixin]:
    """Nested builder for :class:`discord.ui.Section`.

    Add 1-3 text blocks via :meth:`text`, set the accessory via
    :meth:`accessory`, then call :meth:`done`.

    Example::

        layout
            .section()
            .text("# Changelog")
            .text("- Fixed a bug")
            .accessory(ui.Thumbnail("https://example.com/img.png"))
            .done()
    """

    def __init__(self, parent: ParentT) -> None:
        self.parent = parent
        self.children: list[ui.TextDisplay] = []
        self.accessory_item: ui.Item | None = None

    def text(self, content: str) -> Self:
        self.children.append(ui.TextDisplay(content))
        return self

    def accessory(self, item: ui.Item) -> Self:
        """Set the section accessory - must be a Button or Thumbnail."""
        self.accessory_item = item
        return self

    def done(self) -> ParentT:
        if self.accessory_item is None:
            raise ValueError("Section requires an accessory (Button or Thumbnail)")
        self.parent.add(ui.Section(*self.children, accessory=self.accessory_item))
        return self.parent


class ContainerBuilder[ParentT: LayoutMixin](LayoutMixin):
    """Nested builder for :class:`discord.ui.Container`.

    Supports all the same content/layout methods as :class:`LayoutBuilder`.
    Call :meth:`done` to add the container to the parent and return it.

    Example::

        layout
            .container(accent_color=0x5865F2)
            .text("# Encounter!")
            .gallery("https://example.com/img.png")
            .action_row()
                .add(fight_btn)
                .add(flee_btn)
                .done()
            .done()
    """

    def __init__(
        self,
        parent: ParentT,
        *,
        accent_color: discord.Color | int | None = None,
        spoiler: bool = False,
    ) -> None:
        self.parent = parent
        self.children: list[ui.Item] = []
        self.accent_color = accent_color
        self.spoiler = spoiler

    def add(self, item: ui.Item) -> None:
        self.children.append(item)

    def done(self) -> ParentT:
        self.parent.add(
            ui.Container(
                *self.children,
                accent_color=self.accent_color,
                spoiler=self.spoiler,
            )
        )
        return self.parent


# ---------------------------------------------------------------------------
# Root builder
# ---------------------------------------------------------------------------


class LayoutBuilder(LayoutMixin, BaseLayoutView):
    """Fluent builder for Components V2 (IS_COMPONENTS_V2) messages.

    Flat components are added inline.  Layout components that support nesting
    (:meth:`action_row`, :meth:`section`, :meth:`container`) return a child
    builder - call :meth:`done` on it to close the nesting and return this
    view.

    Example::

        layout = (
            LayoutBuilder()
            .text("# Encounter!")
            .container(accent_color=0x5865F2)
                .gallery("https://example.com/img.png")
                .action_row()
                    .add(fight_btn)
                    .add(flee_btn)
                    .done()
                .done()
        )
        await interaction.response.send_message(**layout.to_send_kwargs())
    """

    def add(self, item: ui.Item) -> None:
        self.add_item(item)
