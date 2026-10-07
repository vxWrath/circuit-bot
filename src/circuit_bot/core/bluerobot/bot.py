from typing import Self

import aiohttp
import discord
from discord import app_commands as ac
from discord.ext.commands import Bot

from ..bloxlink import BloxlinkClient
from ..cache import CacheClient
from ..database import DatabasePool
from .tree import CircuitTree

__all__ = ("CircuitBot",)

intents = discord.Intents.none()
intents.guilds = True
intents.emojis = True
intents.members = True

member_cache_flags = discord.MemberCacheFlags.none()
member_cache_flags.joined = True


class CircuitBot(Bot):
    user: discord.ClientUser

    def __init__(self):
        self.session: aiohttp.ClientSession = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(enable_cleanup_closed=True)
        )

        self.database: DatabasePool = DatabasePool(bot=self)
        self.cache: CacheClient = CacheClient(bot=self)
        self.bloxlink: BloxlinkClient = BloxlinkClient(bot=self)

    def initialize(self) -> Self:
        super().__init__(
            command_prefix=[],
            tree_cls=CircuitTree,
            description="A bot for the CircuitBot project.",
            allowed_installs=ac.AppInstallationType(guild=True, user=False),
            intents=intents,
            max_messages=None,
            member_cache_flags=member_cache_flags,
            chunk_guilds_at_startup=False,
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.competing, name="in the circuit"),
        )

        return self
