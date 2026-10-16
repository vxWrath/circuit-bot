import aiohttp
import discord
from discord import app_commands as ac
from discord.ext.commands import Bot

from ..bloxlink import BloxlinkClient
from ..cache import CacheClient
from ..database import DatabasePool
from ..roblox import RobloxClient
from .tree import CircuitTree

__all__ = ("CircuitBot",)

intents = discord.Intents.none()
intents.guilds = True
intents.emojis = True
intents.members = True

member_cache_flags = discord.MemberCacheFlags.none()
member_cache_flags.joined = True


class CircuitBot(Bot):
    """The bot: discord.py's, plus the pieces the bot's own code talks to.

    Everything is built in one go, so an instance is whole the moment it
    exists - ``bot.cache`` and the rest are there to be called, whether or not
    the bot has logged in yet.  Each of them holds the bot rather than the
    other way round, which is why they are built after ``Bot.__init__`` has
    run.

    The session belongs to the bot but is not closed with it: ``Client.close``
    closes the websocket and discord.py's own HTTP session, and the entry point
    closes this one alongside the cache and the database once nothing is left
    running.  The bot is its own async context manager - discord.py's, which
    closes it on the way out however the block ends - and the entry point uses
    that rather than calling ``close`` twice.
    """

    user: discord.ClientUser

    def __init__(self) -> None:
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

        self.session: aiohttp.ClientSession = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(enable_cleanup_closed=True),
            timeout=aiohttp.ClientTimeout(total=10),
        )

        self.database: DatabasePool = DatabasePool(bot=self)
        self.cache: CacheClient = CacheClient(bot=self)
        self.bloxlink: BloxlinkClient = BloxlinkClient(bot=self)
        self.roblox: RobloxClient = RobloxClient(bot=self)
