import hashlib
import time
from collections.abc import Callable, Coroutine, Hashable
from datetime import timedelta
from typing import (
    Literal,
    TypeVar,
)

import discord
from discord.app_commands.commands import CheckInputParameter, Command, ContextMenu
from discord.app_commands.errors import CommandOnCooldown
from msgspec import field

from ..exception import CircuitException
from ..logging import get_logger
from ..model import Model
from .interaction import Interaction

__all__ = (
    "Cooldown",
    "CooldownManager",
    "CooldownManagerNotFound",
    "InteractionCooldownHandler",
    "InteractionCooldowns",
    "cooldown",
    "shared_cooldown",
)

logger = get_logger("cooldowns")

T = TypeVar("T")

CooldownFuncReturn = T | Coroutine[None, None, T]
CooldownKey = Callable[[Interaction], CooldownFuncReturn[T]]


class CooldownManagerNotFound(CircuitException):
    """Raised when a command has no cooldown manager registered."""


class Cooldown(Model):
    limit: int  # attempts allowed per window
    duration: int  # window length in seconds

    window: float = field(default=0.0)  # time of first attempt
    tokens: int | None = field(default=None)  # attempts left

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = self.limit

    def get_tokens(self, current_time: float | None = None) -> int:
        if self.tokens is None:
            raise ValueError("Cooldown bucket is missing tokens value")

        current_time = current_time or time.time()
        tokens_left = max(self.tokens, 0)

        if current_time > self.window + self.duration:
            tokens_left = self.limit

        return tokens_left

    def get_retry_after(self, current_time: float | None = None) -> float:
        current_time = current_time or time.time()
        tokens = self.get_tokens(current_time)

        if tokens == 0:
            return self.duration - (current_time - self.window)
        return 0.0

    def update_rate_limit(self, current_time: float | None = None, *, tokens: int = 1) -> float | None:
        current_time = current_time or time.time()
        self.tokens = self.get_tokens(current_time)

        if self.tokens == self.limit:
            self.window = current_time

        self.tokens -= tokens

        if self.tokens < 0:
            return self.duration - (current_time - self.window)
        return None


class CooldownManager:
    """A cooldown bucket stored in Redis under the manager's id and key.

    The decorator fixes the key and may set default *limit* and *duration*; a
    spend overrides either, re-shaping the bucket, so one manager can enforce
    different windows from different paths.  Checked pre-invoke through
    :meth:`predicate`, which runs before every command run;
    :meth:`InteractionCooldownHandler.spend` reserves a token once the command
    succeeds.  A *shared* manager's id hashes its name alone, so every command
    decorated with the same name checks and spends the same bucket.
    """

    def __init__(
        self,
        key: CooldownKey[Hashable],
        *,
        name: str,
        limit: int | None = None,
        duration: int | None = None,
        shared: bool = False,
    ) -> None:
        self.name = name  # decorator-supplied; names it among a command's cooldowns
        self.id = ""  # set by the decorator
        self.key = key
        self.limit = limit  # decorator defaults; overridable at spend
        self.duration = duration
        self.shared = shared  # set by shared_cooldown; buckets the id by name alone

    async def _key(self, interaction: Interaction) -> str:
        return str(await discord.utils.maybe_coroutine(self.key, interaction))

    async def predicate(self, interaction: Interaction) -> Literal[True]:
        """The pre-invoke check - refuse when the bucket allows no attempts.

        Called before every command run; side-effect free, the command spends
        after it succeeds.
        """
        return await self.predicate_check(interaction, await self._key(interaction))

    async def predicate_check(self, interaction: Interaction, key: str) -> Literal[True]:
        """Pass when the bucket at *key* allows another attempt, raise otherwise."""
        bucket = await interaction.client.cache.get("cooldown", self.id, key, cls=Cooldown)

        if bucket is None:
            return True

        current_time = time.time()

        if bucket.get_tokens(current_time) > 0:
            return True

        retry_after = bucket.get_retry_after(current_time)
        logger.debug("Cooldown hit for command=%s key=%s retry_after=%.1fs", self.id, key, retry_after)
        raise CommandOnCooldown(bucket, retry_after)  # type: ignore

    async def spend(self, interaction: Interaction, *, limit: int | None = None, duration: int | None = None) -> None:
        """Spend a token at the manager's key, under *limit* per *duration*.

        Each falls back to the decorator's default when omitted; a bucket whose
        stored limit or duration differ is reset to these.  A spend with neither
        set and no decorator default raises.  The predicate already refuses
        exhausted buckets pre-invoke; this is the reservation and the guard for
        a call that slips past it.
        """
        limit = self.limit if limit is None else limit
        duration = self.duration if duration is None else duration

        if limit is None or duration is None:
            raise ValueError(
                f"Spending cooldown {self.name!r} needs a limit and duration: set decorator defaults or pass them to spend()"
            )

        await self.spend_token(interaction, await self._key(interaction), limit=limit, duration=duration)

    async def spend_token(
        self, interaction: Interaction, key: str, *, limit: int | None = None, duration: int | None = None
    ) -> bool:
        """Spend one token atomically, resetting the bucket when its shape differs."""

        def change(bucket: Cooldown | None) -> tuple[Cooldown | None, int | None]:
            if limit is None or duration is None:
                return None, None

            if bucket is None or (bucket.limit, bucket.duration) != (limit, duration):
                bucket = Cooldown(limit=limit, duration=duration, window=0.0, tokens=limit)

            bucket.update_rate_limit()
            return bucket, bucket.duration

        spent = await interaction.client.cache.update("cooldown", self.id, key, cls=Cooldown, change=change)
        return spent is not None

    def handler_for(self, interaction: Interaction) -> "InteractionCooldownHandler":
        return InteractionCooldownHandler(self, interaction)


class InteractionCooldownHandler:
    def __init__(self, cooldown_manager: CooldownManager, interaction: Interaction) -> None:
        self.cooldown_manager = cooldown_manager
        self.interaction = interaction

    async def spend(self, *, limit: int | None = None, duration: int | timedelta | None = None) -> None:
        """Check and reserve a token at the manager's key, under *limit* per *duration*.

        Each falls back to the decorator's default when omitted.  The predicate
        already refuses exhausted buckets pre-invoke; this is the reservation
        and the guard for a call that slips past it.
        """
        if isinstance(duration, timedelta):
            duration = int(duration.total_seconds())

        await self.cooldown_manager.spend(self.interaction, limit=limit, duration=duration)


class InteractionCooldowns:
    """Named access to the cooldown managers on a command.

    Every cooldown is decorated with a name; the command body reaches each
    through this accessor, ``interaction.cooldowns["<name>"]``.
    """

    def __init__(self, interaction: Interaction, command_name: str) -> None:
        self.interaction = interaction
        self.command_name = command_name

    def __getitem__(self, name: str) -> InteractionCooldownHandler:
        manager = self.interaction.client.get_cooldown_manager(self.command_name, name=name)
        return manager.handler_for(self.interaction)

    def __getattr__(self, name: str) -> InteractionCooldownHandler:
        """Attribute sugar - ``cooldowns.bloxlink``."""
        try:
            return self[name]
        except CooldownManagerNotFound:
            raise AttributeError(f"No cooldown named {name!r} on {self.command_name!r}") from None


def cooldown_check(manager: CooldownManager) -> Callable[[T], T]:
    def decorator(func: CheckInputParameter) -> CheckInputParameter:
        target = func.callback if isinstance(func, (Command, ContextMenu)) else func

        # The id namespaces the bucket in Redis.  A shared manager hashes its
        # name alone, so every command decorated with it shares one bucket.
        if manager.shared:
            manager.id = hashlib.md5(f"shared:{manager.name}".encode()).hexdigest()
        else:
            manager.id = hashlib.md5(f"{target.__qualname__}:{manager.name}".encode()).hexdigest()

        # The predicate runs before every command run; the command spends a
        # token once it succeeds.
        if isinstance(func, (Command, ContextMenu)):
            func.checks.append(manager.predicate)  # type: ignore
        else:
            if not hasattr(func, "__discord_app_commands_checks__"):
                func.__discord_app_commands_checks__ = []

            func.__discord_app_commands_checks__.append(manager.predicate)

        managers = getattr(target, "__cooldown_managers__", None)
        if managers is None:
            managers = []
            target.__cooldown_managers__ = managers  # type: ignore

        if any(existing.name == manager.name for existing in managers):
            raise ValueError(f"{target.__qualname__!r} already has a cooldown named {manager.name!r}")

        managers.append(manager)
        return func

    return decorator  # type: ignore


def _make_manager(
    key: CooldownKey[Hashable] | None,
    *,
    name: str,
    limit: int | None,
    duration: int | timedelta | None,
    shared: bool,
) -> CooldownManager:
    """Build the manager behind :func:`cooldown` and :func:`shared_cooldown`."""
    if key is discord.utils.MISSING:
        key_func = lambda i: i.user.id
    elif key is None:
        key_func = lambda _: None
    else:
        key_func = key

    if isinstance(duration, timedelta):
        duration = int(duration.total_seconds())

    return CooldownManager(key_func, name=name, limit=limit, duration=duration, shared=shared)


def cooldown(
    name: str,
    /,
    limit: int | None = None,
    duration: int | timedelta | None = None,
    *,
    key: CooldownKey[Hashable] | None = discord.utils.MISSING,
) -> Callable[[T], T]:
    """A cooldown whose limit and duration default here, decided at spend time when left out.

    *name* names the manager among the command's cooldowns; the command body
    reaches it through ``interaction.cooldowns[name]``.  *key* is the bucket's
    key, fixed at decoration time - the invoker by default.  The bucket is
    checked pre-invoke; the spend reserves the token under its limit and
    duration, overriding either default.
    """
    return cooldown_check(_make_manager(key, name=name, limit=limit, duration=duration, shared=False))


def shared_cooldown(
    name: str,
    /,
    limit: int | None = None,
    duration: int | timedelta | None = None,
    *,
    key: CooldownKey[Hashable] | None = discord.utils.MISSING,
) -> Callable[[T], T]:
    """A cooldown shared by every command decorated with the same *name*.

    Takes the same arguments as :func:`cooldown`; each decoration is its own
    manager, but all of them hash the name alone for the bucket id, so they
    check and spend one bucket.  Keep the defaults consistent per name - a
    spend falls back to the decorator of the command running it.
    """
    return cooldown_check(_make_manager(key, name=name, limit=limit, duration=duration, shared=True))
