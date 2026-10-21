import hashlib
import time
from collections.abc import Callable, Coroutine, Hashable
from datetime import timedelta
from typing import (
    Literal,
    TypeVar,
    cast,
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
    "CooldownError",
    "CooldownManager",
    "CooldownManagerNotFound",
    "DynamicCooldownManager",
    "InteractionCooldownHandler",
    "InteractionCooldowns",
    "InteractionDynamicCooldownHandler",
    "InteractionRegularCooldownHandler",
    "RegularCooldownManager",
    "cooldown",
    "dynamic_cooldown",
)

logger = get_logger("cooldowns")

T = TypeVar("T")

CooldownFuncReturn = T | Coroutine[None, None, T]
CooldownKey = Callable[[Interaction], CooldownFuncReturn[T]]


class CooldownError(CircuitException):
    """Base for cooldown errors."""


class CooldownManagerNotFound(CooldownError):
    """Raised when a command has no cooldown manager registered."""


class Cooldown(Model):
    rate: int  # attempts possible
    per: int  # how many attemps every certain amount of time

    window: float = field(default=0.0)  # time of first attempt
    tokens: int | None = field(default=None)  # attempts left

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = self.rate

    def get_tokens(self, current_time: float | None = None) -> int:
        if self.tokens is None:
            raise ValueError("Cooldown bucket is missing tokens value")

        current_time = current_time or time.time()
        tokens_left = max(self.tokens, 0)

        if current_time > self.window + self.per:
            tokens_left = self.rate

        return tokens_left

    def get_retry_after(self, current_time: float | None = None) -> float:
        current_time = current_time or time.time()
        tokens = self.get_tokens(current_time)

        if tokens == 0:
            return self.per - (current_time - self.window)
        return 0.0

    def update_rate_limit(self, current_time: float | None = None, *, tokens: int = 1) -> float | None:
        current_time = current_time or time.time()
        self.tokens = self.get_tokens(current_time)

        if self.tokens == self.rate:
            self.window = current_time

        self.tokens -= tokens

        if self.tokens < 0:
            return self.per - (current_time - self.window)
        return None


class CooldownManager:
    """A cooldown bucket stored in Redis under the manager's command id and key.

    The two shapes subclass this.  A :class:`RegularCooldownManager` fixes the
    key, rate, and window at decoration time; a :class:`DynamicCooldownManager`
    fixes only the key and decides the rate and window when it is spent.  Both
    check pre-invoke through :meth:`predicate`, which runs before every command
    run.
    """

    def __init__(self, *, name: str) -> None:
        self.name = name  # decorator-supplied; names it among a command's cooldowns
        self.id = ""  # set by the decorator

    async def predicate(self, interaction: Interaction) -> Literal[True]:
        """The pre-invoke check - each shape checks the bucket it knows.

        Called before every command run; side-effect free, the command spends
        after it succeeds.
        """
        raise NotImplementedError

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

    async def spend_token(
        self, interaction: Interaction, key: str, *, rate: int | None = None, per: int | None = None
    ) -> bool:
        """Spend one token atomically, creating the bucket if *rate* and *per* are given."""

        def change(bucket: Cooldown | None) -> tuple[Cooldown | None, int | None]:
            if bucket is None:
                if rate is None or per is None:
                    return None, None
                bucket = Cooldown(rate=rate, per=per, window=0.0, tokens=rate)

            bucket.update_rate_limit()
            return bucket, bucket.per

        spent = await interaction.client.cache.update("cooldown", self.id, key, cls=Cooldown, change=change)
        return spent is not None

    def handler_for(self, interaction: Interaction) -> "InteractionCooldownHandler":
        return InteractionCooldownHandler(self, interaction)


class RegularCooldownManager(CooldownManager):
    """A cooldown fixed at decoration time: key, rate, and window.

    Checked pre-invoke against the bucket at ``key(interaction)``, and spent
    with :meth:`InteractionRegularCooldownHandler.spend` once the command
    succeeds - so a failed run doesn't eat the window.
    """

    def __init__(self, key: CooldownKey[Hashable], *, rate: int, per: int, name: str) -> None:
        super().__init__(name=name)

        self.key = key
        self.rate = rate
        self.per = per

    async def spend(self, interaction: Interaction) -> None:
        key = str(await discord.utils.maybe_coroutine(self.key, interaction))
        await self.spend_token(interaction, key, rate=self.rate, per=self.per)

    async def predicate(self, interaction: Interaction) -> Literal[True]:
        key = str(await discord.utils.maybe_coroutine(self.key, interaction))
        return await self.predicate_check(interaction, key)


class DynamicCooldownManager(CooldownManager):
    """A cooldown whose rate and window are decided when it is spent.

    One bucket, at the key fixed at decoration time, checked pre-invoke by
    :meth:`predicate`.  :meth:`InteractionDynamicCooldownHandler.spend`
    reserves a token under the given *rate* and *per* - checking the bucket
    first, so a call that slipped past the predicate still refuses.
    """

    def __init__(self, key: CooldownKey[Hashable], *, name: str) -> None:
        super().__init__(name=name)

        self.key = key

    async def _key(self, interaction: Interaction) -> str:
        return str(await discord.utils.maybe_coroutine(self.key, interaction))

    async def predicate(self, interaction: Interaction) -> Literal[True]:
        return await self.predicate_check(interaction, await self._key(interaction))

    async def spend(self, interaction: Interaction, *, rate: int, per: int) -> None:
        resolved = await self._key(interaction)
        bucket = await interaction.client.cache.get("cooldown", self.id, resolved, cls=Cooldown)

        if bucket is not None:
            current_time = time.time()

            if bucket.get_tokens(current_time) == 0:
                retry_after = bucket.get_retry_after(current_time)
                logger.debug("Cooldown hit for command=%s key=%s retry_after=%.1fs", self.id, resolved, retry_after)
                raise CommandOnCooldown(bucket, retry_after)  # type: ignore

        await self.spend_token(interaction, resolved, rate=rate, per=per)


class InteractionCooldownHandler:
    def __init__(self, cooldown_manager: CooldownManager, interaction: Interaction) -> None:
        self.cooldown_manager = cooldown_manager
        self.interaction = interaction

    @property
    def regular(self) -> "InteractionRegularCooldownHandler":
        """Use the manager's regular bucket."""
        if not isinstance(self.cooldown_manager, RegularCooldownManager):
            raise CooldownError("This command uses a dynamic cooldown; call .dynamic.spend(rate=..., per=..., key=...)")
        return InteractionRegularCooldownHandler(self)

    @property
    def dynamic(self) -> "InteractionDynamicCooldownHandler":
        """Use the manager's dynamic bucket."""
        if not isinstance(self.cooldown_manager, DynamicCooldownManager):
            raise CooldownError("This command uses a regular cooldown; call .regular.spend()")
        return InteractionDynamicCooldownHandler(self)


class InteractionRegularCooldownHandler:
    def __init__(self, parent: InteractionCooldownHandler) -> None:
        self.parent = parent

    async def spend(self) -> None:
        """Spend a token, creating the bucket if it is missing."""
        manager = cast(RegularCooldownManager, self.parent.cooldown_manager)
        await manager.spend(self.parent.interaction)


class InteractionDynamicCooldownHandler:
    def __init__(self, parent: InteractionCooldownHandler) -> None:
        self.parent = parent

    async def spend(self, *, rate: int, per: int | timedelta) -> None:
        """Check and reserve a token at the manager's key, under *rate* per *per*.

        The predicate already refuses exhausted buckets pre-invoke; this is the
        reservation and the guard for a call that slips past it.
        """
        if isinstance(per, timedelta):
            per = int(per.total_seconds())

        manager = cast(DynamicCooldownManager, self.parent.cooldown_manager)
        await manager.spend(self.parent.interaction, rate=rate, per=per)


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
        manager.id = hashlib.md5(f"{target.__qualname__}:{manager.name}".encode()).hexdigest()

        # The predicate runs before every command run.  A dynamic manager
        # checks its fallback bucket there; its other keys enforce at spend.
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


def cooldown(
    rate: int,
    per: int | timedelta,
    *,
    name: str,
    key: CooldownKey[Hashable] | None = discord.utils.MISSING,
) -> Callable[[T], T]:
    if key is discord.utils.MISSING:
        key_func = lambda i: i.user.id
    elif key is None:
        key_func = lambda i: None
    else:
        key_func = key

    if isinstance(per, timedelta):
        per = int(per.total_seconds())

    manager = RegularCooldownManager(key_func, rate=rate, per=per, name=name)

    return cooldown_check(manager)


def dynamic_cooldown(*, name: str, key: CooldownKey[Hashable] | None = discord.utils.MISSING) -> Callable[[T], T]:
    """A cooldown whose rate and window are supplied when it is spent.

    *name* names the manager among the command's cooldowns; the command body
    reaches it through ``interaction.cooldowns[name]``.  *key* is the bucket's
    key, fixed at decoration time like a regular cooldown's - the invoker by
    default.  The bucket is checked pre-invoke; the spend reserves the token
    under its rate and window.
    """
    if key is discord.utils.MISSING:
        key_func = lambda i: i.user.id
    elif key is None:
        key_func = lambda i: None
    else:
        key_func = key

    manager = DynamicCooldownManager(key_func, name=name)

    return cooldown_check(manager)
