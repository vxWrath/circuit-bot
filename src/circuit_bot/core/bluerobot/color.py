from typing import Self

import discord

__all__ = ["Color", "CommonEmojis"]


class CommonEmojis:
    FAIL = "\N{CROSS MARK}"
    SUCCESS = "\N{WHITE HEAVY CHECK MARK}"
    LOADING = "\N{HOURGLASS WITH FLOWING SAND}"
    WARNING = "\N{WARNING SIGN}"


class Color(discord.Color):
    @classmethod
    def fallback(cls, color: discord.Color) -> Self:
        return cls.dark_embed() if color.value == 0 else cls(color.value)

    @classmethod
    def white(cls) -> Self:
        return cls(0xFFFFFF)

    @classmethod
    def black(cls) -> Self:
        return cls(0x000001)

    @classmethod
    def circuit_blue(cls) -> Self:
        return cls(0x408DFF)
