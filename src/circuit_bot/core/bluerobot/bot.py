import inspect
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import aiohttp
import discord
from discord.app_commands import (
    AppCommand,
    AppCommandGroup,
    AppInstallationType,
    Command,
    ContextMenu,
    Group,
)
from discord.ext import commands

from ...config import PACKAGE_PATH, BotConfig, get_bot_config
from ..bloxlink import BloxlinkClient
from ..cache import CacheClient
from ..database import DatabasePool
from ..env import is_dev
from ..logging import get_logger
from ..roblox import RobloxClient
from ..service import Service, ServiceNotFound
from .app_commands import CachedAppCommand, CommandNotFound
from .color import Color
from .cooldown import CooldownManager, CooldownManagerNotFound
from .errors import ErrorHandler
from .interaction import BaseItem
from .tree import CircuitTree

__all__ = ("CircuitBot",)

intents = discord.Intents.none()
intents.guilds = True
intents.emojis = True
intents.members = True

member_cache_flags = discord.MemberCacheFlags.none()
member_cache_flags.joined = True


logger = get_logger("bot")


class CircuitBot(commands.Bot):
    """The bot: discord.py's, plus the pieces the bot's own code talks to.

    Everything is built in one go, so ``bot.cache`` and the rest are there
    whether or not the bot has logged in yet.  The session belongs to the bot
    but is not closed with it: the entry point closes it alongside the cache
    and the database once nothing is left running.
    """

    user: discord.ClientUser
    config: BotConfig

    def __init__(self) -> None:
        super().__init__(
            command_prefix=[],
            tree_cls=CircuitTree,
            description="A bot for the CircuitBot project.",
            allowed_installs=AppInstallationType(guild=True, user=False),
            intents=intents,
            max_messages=None,
            member_cache_flags=member_cache_flags,
            chunk_guilds_at_startup=False,
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.competing, name="in the circuit"),
        )

        self.config = get_bot_config()
        self.guild_obj = discord.Object(id=self.config.guild_id, type=discord.Guild)
        self.extension_names: list[str] = []
        self.app_commands_cache: dict[str, CachedAppCommand] = {}
        self.cooldown_managers: dict[str, list[CooldownManager]] = {}
        self.services: dict[type[Service], Service] = {}

        self.session: aiohttp.ClientSession = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(enable_cleanup_closed=True),
            timeout=aiohttp.ClientTimeout(total=10),
        )

        # Each webhook posts through the bot's own session, which the entry point closes.
        self.stored_webhooks: dict[str, discord.Webhook] = {
            "error": discord.Webhook.from_url(self.config.error_webhook_url, session=self.session),
            "connection": discord.Webhook.from_url(self.config.connection_webhook_url, session=self.session),
        }

        self.database: DatabasePool = DatabasePool(bot=self)
        self.cache: CacheClient = CacheClient(bot=self)
        self.bloxlink: BloxlinkClient = BloxlinkClient(bot=self)
        self.roblox: RobloxClient = RobloxClient(bot=self)
        self.error_handler = ErrorHandler(bot=self)

    async def setup_hook(self) -> None:
        first_sync = not self.config.sync_sentinel.exists()

        if first_sync and is_dev():
            # The tree is empty here, so this clears the globals left by an earlier sync.
            await self.tree.sync()

        await self.load_commands_and_events()

        if first_sync:
            if is_dev():
                self.tree.copy_global_to(guild=self.guild_obj)
                app_commands = await self.tree.sync(guild=self.guild_obj)
            else:
                app_commands = await self.tree.sync()

            self.config.sync_sentinel.parent.mkdir(parents=True, exist_ok=True)
            self.config.sync_sentinel.touch()
            logger.info("Synced commands")
        else:
            app_commands = await self.fetch_app_commands()

        self.app_commands_cache = self.fill_app_commands_cache(app_commands)
        self.attach_cooldown_managers()

        embed = discord.Embed(
            description=(
                f"## Bot Startup\n`Bot:` **{self.user!s}** (`{self.user.id}`)\n`Guild:` `{self.guild_obj.id}`\n"
            ),
            color=Color.brand_green(),
            timestamp=discord.utils.utcnow(),
        )

        await send_connection_webhook(self, embed=embed)

    async def close(self) -> None:
        await super().close()

        embed = discord.Embed(
            description=(
                f"## Bot Shutdown\n`Bot:` **{self.user!s}** (`{self.user.id}`)\n`Guild:` `{self.guild_obj.id}`\n"
            ),
            color=Color.brand_red(),
            timestamp=discord.utils.utcnow(),
        )

        await send_connection_webhook(self, embed=embed)

    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        """Report an exception raised by an event listener."""

        error = sys.exc_info()[1]

        if isinstance(error, Exception):
            await self.error_handler.handle(None, error, source=event_method)

    @staticmethod
    def extension_name(file_path: Path) -> str:
        """The dotted module path an extension file is imported as."""

        relative_path = file_path.relative_to(PACKAGE_PATH.parent).with_suffix("")
        return ".".join(relative_path.parts)

    async def load_commands_and_events(self) -> list[str]:
        """Load all commands and events from the command and event paths."""
        self.extension_names = []

        for file_path in self.config.command_path.glob("*.py"):
            if file_path.name == "__init__.py":
                continue

            cog_path = self.extension_name(file_path)

            try:
                await self.reload_extension(cog_path)
                logger.info("Reloaded command extension %s", cog_path)
            except commands.ExtensionNotLoaded:
                await self.load_extension(cog_path)
                logger.info("Loaded command extension %s", cog_path)

            self.extension_names.append(cog_path)
            self.find_dynamic_items(cog_path)

        for file_path in self.config.event_path.glob("*.py"):
            if file_path.name == "__init__.py":
                continue

            cog_path = self.extension_name(file_path)

            try:
                await self.reload_extension(cog_path)
                logger.info("Reloaded event extension %s", cog_path)
            except commands.ExtensionNotLoaded:
                await self.load_extension(cog_path)
                logger.info("Loaded event extension %s", cog_path)

            self.extension_names.append(cog_path)

        self.fill_services()

        return self.extension_names

    async def unload_commands_and_events(self) -> None:
        """Unload all commands and events from the command and event paths."""

        for cog_name in self.extension_names:
            try:
                await self.unload_extension(cog_name)
                logger.info("Unloaded command extension %s", cog_name)
            except commands.ExtensionNotLoaded:
                pass

        self.extension_names = []

    def find_dynamic_items(self, cog_path: str) -> None:
        for name, obj in inspect.getmembers(sys.modules[cog_path]):
            if not isinstance(obj, type):
                continue

            if issubclass(obj, BaseItem) and obj is not BaseItem:
                self.add_dynamic_items(obj)
                logger.info("Found dynamic item %s.%s", cog_path, name)

        return None

    async def fetch_app_commands(
        self, *, guild: discord.Object | None = discord.utils.MISSING
    ) -> Sequence[AppCommand | AppCommandGroup]:
        """Fetch the commands registered with Discord, for the dev guild or globally."""

        if guild is discord.utils.MISSING:
            guild = self.guild_obj if is_dev() else None

        return await self.tree.fetch_commands(guild=guild)

    def fill_app_commands_cache(
        self, app_commands: Sequence[AppCommand | AppCommandGroup]
    ) -> dict[str, CachedAppCommand]:
        cache: dict[str, CachedAppCommand] = {}

        for command in self.flatten_app_commands(app_commands):
            name = getattr(command, "qualified_name", command.name)

            if isinstance(command, AppCommand):
                root = command
            elif isinstance(command.parent, AppCommand):
                root = command.parent
            elif isinstance(command.parent.parent, AppCommand):
                root = command.parent.parent
            else:
                raise ValueError(f"Unexpected command nesting for {name!r}")

            cache[name] = CachedAppCommand(id=root.id, name=name)

        return cache

    def flatten_app_commands(
        self, entities: Sequence[AppCommand | AppCommandGroup]
    ) -> Sequence[AppCommand | AppCommandGroup]:
        commands = []
        for entity in entities:
            is_group = any(isinstance(opt, AppCommandGroup) for opt in entity.options)

            if is_group:
                commands.extend(self.flatten_app_commands(entity.options))  # type: ignore
            else:
                commands.append(entity)

        return commands

    def attach_cooldown_managers(self) -> None:
        self.cooldown_managers = {}
        self.get_cooldown_managers(self.cooldown_managers, self.tree.get_commands(guild=None))

        for command_name in self.cooldown_managers:
            if command_name in self.app_commands_cache:
                logger.info("Attached cooldown manager to command %r", command_name)
            else:
                logger.warning("No synced app command matches cooldown manager %r", command_name)

    def get_cooldown_managers(
        self,
        cooldown_managers: dict[str, list[CooldownManager]],
        entities: Sequence[Command[Any, ..., Any] | Group | ContextMenu],
    ) -> None:
        for entity in entities:
            if isinstance(entity, (Command, ContextMenu)):
                managers = getattr(entity.callback, "__cooldown_managers__", None)

                if managers is not None:
                    name = getattr(entity, "qualified_name", entity.name)
                    cooldown_managers[name] = managers

            elif isinstance(entity, Group):
                self.get_cooldown_managers(cooldown_managers, entity.commands)

    def get_cooldown_manager(self, command_name: str, *, name: str) -> CooldownManager:
        """The cooldown manager named *name* on *command_name*, raising if it has none."""

        try:
            managers = self.cooldown_managers[command_name]
        except KeyError:
            raise CooldownManagerNotFound(f"No cooldown managers are registered for {command_name!r}") from None

        for manager in managers:
            if manager.name == name:
                return manager

        raise CooldownManagerNotFound(
            f"No cooldown named {name!r} on {command_name!r}; available: {[manager.name for manager in managers]!r}"
        )

    def retrieve_command(self, name: str) -> CachedAppCommand:
        """Retrieve a command from the cache."""
        try:
            return self.app_commands_cache[name]
        except KeyError:
            raise CommandNotFound(name) from None

    def fill_services(self) -> None:
        self.services = {}

        for cls in Service.__subclasses__():
            self.services[cls] = cls(bot=self)

    def get_service[T: Service](self, cls: type[T]) -> T:
        """Get a service by its class."""

        try:
            # fill_services keys each service by its own class, so the value is the caller's T.
            return cast(T, self.services[cls])
        except KeyError:
            raise ServiceNotFound(cls) from None


async def send_connection_webhook(bot: CircuitBot, embed: discord.Embed) -> None:
    """Send a webhook to the bot's connection channel."""

    try:
        await bot.stored_webhooks["connection"].send(embed=embed)
    except Exception as e:
        logger.error("Failed to send webhook", exc_info=e)
