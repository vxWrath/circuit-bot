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

    def _add(self, item: ui.Item) -> None:
        raise NotImplementedError

    def text(self, content: str) -> Self:
        self._add(ui.TextDisplay(content))
        return self

    def separator(
        self,
        *,
        visible: bool = True,
        spacing: SeparatorSpacing = SeparatorSpacing.small,
    ) -> Self:
        self._add(ui.Separator(visible=visible, spacing=spacing))
        return self

    def gallery(
        self,
        *items: MediaSource | MediaGalleryItem,
        spoiler: bool = False,
    ) -> Self:
        gallery_items: list[MediaGalleryItem] = [
            item if isinstance(item, MediaGalleryItem) else MediaGalleryItem(item, spoiler=spoiler) for item in items
        ]
        self._add(ui.MediaGallery(*gallery_items))
        return self

    def file(self, url: str, *, spoiler: bool = False) -> Self:
        self._add(ui.File(url, spoiler=spoiler))
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
        self._parent = parent
        self._items: list[ui.Item] = []

    def add(self, item: ui.Item) -> Self:
        self._items.append(item)
        return self

    def done(self) -> ParentT:
        self._parent._add(ui.ActionRow(*self._items))
        return self._parent


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
        self._parent = parent
        self._children: list[ui.TextDisplay] = []
        self._accessory: ui.Item | None = None

    def text(self, content: str) -> Self:
        self._children.append(ui.TextDisplay(content))
        return self

    def accessory(self, item: ui.Item) -> Self:
        """Set the section accessory - must be a Button or Thumbnail."""
        self._accessory = item
        return self

    def done(self) -> ParentT:
        if self._accessory is None:
            raise ValueError("Section requires an accessory (Button or Thumbnail)")
        self._parent._add(ui.Section(*self._children, accessory=self._accessory))
        return self._parent


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
        self._parent = parent
        self._children: list[ui.Item] = []
        self._accent_color = accent_color
        self._spoiler = spoiler

    def _add(self, item: ui.Item) -> None:
        self._children.append(item)

    def done(self) -> ParentT:
        self._parent._add(
            ui.Container(
                *self._children,
                accent_color=self._accent_color,
                spoiler=self._spoiler,
            )
        )
        return self._parent


# ---------------------------------------------------------------------------
# Root builder
# ---------------------------------------------------------------------------


class LayoutBuilder(LayoutMixin, BaseLayoutView):
    """Fluent builder for Components V2 (IS_COMPONENTS_V2) messages.

    Flat components are added inline; layout components that support nesting
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

    def _add(self, item: ui.Item) -> None:
        self.add_item(item)

    def thumbnail(
        self,
        media: MediaSource,
        *,
        description: str | None = None,
        spoiler: bool = False,
    ) -> Self:
        """Add a standalone :class:`discord.ui.Thumbnail`.

        Thumbnails are typically used as a section accessory; pass a
        ``ui.Thumbnail(...)`` instance directly to :meth:`SectionBuilder.accessory`
        for that use-case.
        """
        self._add(ui.Thumbnail(media, description=description, spoiler=spoiler))
        return self
