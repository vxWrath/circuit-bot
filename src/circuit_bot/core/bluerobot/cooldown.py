import hashlib
import time
from collections.abc import Callable, Coroutine, Hashable
from typing import (
    Literal,
    TypeVar,
    cast,
    overload,
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
    "InteractionChangeableCooldownHandler",
    "InteractionCooldownHandler",
    "InteractionRegularCooldownHandler",
    "changeable_cooldown",
    "cooldown",
)

logger = get_logger("cooldowns")

T = TypeVar("T")

CooldownFuncReturn = T | Coroutine[None, None, T]
CooldownKey = Callable[[Interaction], CooldownFuncReturn[T]]

ChangeableCooldownFactory = Callable[[Interaction, int, int], CooldownFuncReturn[T]]


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
    """A cooldown bucket stored in Redis under the manager's command id and key."""

    @overload
    def __init__(
        self,
        key: CooldownKey[Hashable],
        *,
        rate: int,
        per: int,
        changeable: Literal[False],
    ) -> None: ...

    @overload
    def __init__(
        self,
        key: CooldownKey[Hashable],
        factory: ChangeableCooldownFactory[Cooldown | None],
        *,
        rate: int,
        per: int,
        changeable: Literal[True],
    ) -> None: ...

    def __init__(
        self,
        key: CooldownKey[Hashable],
        factory: ChangeableCooldownFactory[Cooldown | None] | None = None,
        *,
        rate: int,
        per: int,
        changeable: bool,
    ) -> None:
        if changeable and factory is None:
            raise CooldownError("A changeable cooldown manager needs a factory")

        self.id = ""  # will be set in the decorator

        self.key = key
        self.factory = factory
        self.rate = rate
        self.per = per

        self.changeable = changeable

    async def spend_regular(self, interaction: Interaction) -> None:
        key = str(await discord.utils.maybe_coroutine(self.key, interaction))
        await self.spend_token(interaction, key, rate=self.rate, per=self.per)

    async def spend_changeable(self, interaction: Interaction, rate: int, per: int) -> None:
        key = str(await discord.utils.maybe_coroutine(self.key, interaction))

        if await self.spend_token(interaction, key):
            return

        factory = cast(ChangeableCooldownFactory[Cooldown | None], self.factory)
        bucket = await discord.utils.maybe_coroutine(factory, interaction, rate, per)

        if bucket is not None:
            await self.spend_token(interaction, key, rate=bucket.rate, per=bucket.per)

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

    async def predicate(self, interaction: Interaction) -> Literal[True]:
        # Checking is side-effect free; the command spends with .spend() after it succeeds.
        key = str(await discord.utils.maybe_coroutine(self.key, interaction))
        bucket = await interaction.client.cache.get("cooldown", self.id, key, cls=Cooldown)

        if bucket is None:
            return True

        current_time = time.time()

        if bucket.get_tokens(current_time) > 0:
            return True

        retry_after = bucket.get_retry_after(current_time)
        logger.debug("Cooldown hit for command=%s key=%s retry_after=%.1fs", self.id, key, retry_after)
        raise CommandOnCooldown(bucket, retry_after)  # type: ignore

    def handler_for(self, interaction: Interaction) -> "InteractionCooldownHandler":
        return InteractionCooldownHandler(self, interaction)


class InteractionCooldownHandler:
    def __init__(self, cooldown_manager: CooldownManager, interaction: Interaction) -> None:
        self.cooldown_manager = cooldown_manager
        self.interaction = interaction

    @property
    def regular(self) -> "InteractionRegularCooldownHandler":
        """Use the manager's regular bucket."""
        if self.cooldown_manager.changeable:
            raise CooldownError("This command uses a changeable cooldown; call .changeable.spend(rate, per)")
        return InteractionRegularCooldownHandler(self)

    @property
    def changeable(self) -> "InteractionChangeableCooldownHandler":
        """Use the manager's changeable bucket."""
        if not self.cooldown_manager.changeable:
            raise CooldownError("This command uses a regular cooldown; call .regular.spend()")
        return InteractionChangeableCooldownHandler(self)


class InteractionRegularCooldownHandler:
    def __init__(self, parent: InteractionCooldownHandler) -> None:
        self.parent = parent

    async def spend(self) -> None:
        """Spend a token, creating the bucket if it is missing."""
        await self.parent.cooldown_manager.spend_regular(self.parent.interaction)


class InteractionChangeableCooldownHandler:
    def __init__(self, parent: InteractionCooldownHandler) -> None:
        self.parent = parent

    async def spend(self, rate: int, per: int) -> None:
        """Spend a token, creating the bucket at *rate* per *per* seconds if it is missing."""
        await self.parent.cooldown_manager.spend_changeable(self.parent.interaction, rate, per)


def cooldown_check(manager: CooldownManager) -> Callable[[T], T]:
    def decorator(func: CheckInputParameter) -> CheckInputParameter:
        target = func.callback if isinstance(func, (Command, ContextMenu)) else func
        manager.id = hashlib.md5(target.__qualname__.encode()).hexdigest()

        if isinstance(func, (Command, ContextMenu)):
            func.checks.append(manager.predicate)  # type: ignore
        else:
            if not hasattr(func, "__discord_app_commands_checks__"):
                func.__discord_app_commands_checks__ = []

            func.__discord_app_commands_checks__.append(manager.predicate)

        target.__cooldown_manager__ = manager  # type: ignore
        return func

    return decorator  # type: ignore


def cooldown(rate: int, per: int, *, key: CooldownKey[Hashable] | None = discord.utils.MISSING) -> Callable[[T], T]:
    if key is discord.utils.MISSING:
        key_func = lambda i: i.user.id
    elif key is None:
        key_func = lambda i: None
    else:
        key_func = key

    manager = CooldownManager(key_func, rate=rate, per=per, changeable=False)

    return cooldown_check(manager)


def changeable_cooldown(
    rate: int, per: int, *, key: CooldownKey[Hashable] | None = discord.utils.MISSING
) -> Callable[[T], T]:
    if key is discord.utils.MISSING:
        key_func = lambda i: i.user.id
    elif key is None:
        key_func = lambda i: None
    else:
        key_func = key

    factory = lambda i, r, p: Cooldown(rate=r, per=p, window=0.0, tokens=r)
    manager = CooldownManager(key_func, factory, rate=rate, per=per, changeable=True)

    return cooldown_check(manager)
