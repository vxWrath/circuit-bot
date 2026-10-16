from typing import Any, get_origin

import msgspec

from .exceptions import CacheValidationError

__all__ = ("CacheCodec",)


class CacheCodec:
    """Turn cache values into bytes and back again.

    Reading is strict: a cached value must match the shape it was cached as, so
    an entry written by an earlier version of a model is refused.  Any
    :class:`msgspec.Struct` will do - inheriting
    :class:`~circuit_bot.core.model.Model` is a convention of the bot's, not a
    requirement.
    """

    def __init__(self) -> None:
        self._encoder = msgspec.json.Encoder()
        self._decoders: dict[Any, msgspec.json.Decoder] = {}

    @staticmethod
    def type_name(obj: type[Any]) -> str:
        """Render *obj* for error messages: ``User``, ``dict[str, str]``."""

        return obj.__name__ if isinstance(obj, type) else str(obj)

    @staticmethod
    def is_struct_type(obj: type[Any]) -> bool:
        """Whether *obj* is a struct - something msgspec knows how to build."""

        return isinstance(obj, type) and issubclass(obj, msgspec.Struct)

    @classmethod
    def validate_cls(
        cls,
        as_type: type[Any],
        *,
        scalars: tuple[type[Any], ...] = (),
        containers: tuple[type[Any], ...] = (),
    ) -> None:
        """Reject an unsupported *cls*.

        ``bytes``, *scalars*, *containers* and their generic forms
        (``dict[str, T]``, ``list[T]``), and :class:`msgspec.Struct` subclasses
        are allowed; anything else raises ``TypeError``.
        """

        if as_type is bytes or as_type in scalars:
            return

        origin = get_origin(as_type)
        if any(as_type is c or origin is c for c in containers):
            return

        if cls.is_struct_type(as_type):
            return

        raise TypeError(f"Unsupported cls {as_type!r}")

    def get_decoder[T](self, cls: type[T]) -> msgspec.json.Decoder:
        """The decoder for *cls*, built on first ask and kept - building one compiles it."""

        decoder = self._decoders.get(cls)

        if decoder is None:
            decoder = msgspec.json.Decoder(type=cls)
            self._decoders[cls] = decoder

        return decoder

    def encode(self, value: msgspec.Struct | dict[str, Any] | list[Any] | str | int | float | bool | bytes) -> bytes:
        """Encode *value* for storage.

        ``bytes`` are stored as-is; structs, ``dict``, ``list`` and JSON scalars
        are JSON-encoded.  Any other value type raises ``TypeError``.
        """

        if isinstance(value, bytes):
            return value

        if type(value) in (dict, list, str, int, float, bool) or isinstance(value, msgspec.Struct):
            return self._encoder.encode(value)

        raise TypeError(f"Unsupported value type {type(value)!r}")

    def decode(self, data: bytes, cls: type[Any], *, key: str) -> Any:
        """Decode *data* according to *cls*.

        ``bytes`` pass through and ``int`` is parsed with ``int()``, which is how
        a counter from :meth:`~circuit_bot.core.cache.client.CacheClient.incr`
        reads back.  *key* names the entry in errors, and a payload that no
        longer matches *cls* raises :class:`CacheValidationError`.
        """

        if cls is bytes:
            return data

        if cls is int:
            try:
                return int(data)
            except ValueError as e:
                raise CacheValidationError(f"Cached value at {key!r} isn't an int: {e}") from e

        decoder = self.get_decoder(cls)

        try:
            return decoder.decode(data)
        except msgspec.DecodeError as e:
            raise CacheValidationError(f"Cached value at {key!r} doesn't match {self.type_name(cls)}: {e}") from e

    def encode_hash(self, instance: msgspec.Struct | dict[str, Any]) -> dict[str, bytes]:
        """JSON-encode every field of *instance* on its own.

        Each field is encoded independently, so a reader can fetch and decode a
        subset of them.
        """

        fields: dict[str, Any] = msgspec.to_builtins(instance) if isinstance(instance, msgspec.Struct) else instance

        return {field: self._encoder.encode(value) for field, value in fields.items()}

    def decode_hash(self, mapping: dict[str, bytes] | dict[bytes, bytes], cls: type[Any], *, key: str) -> Any:
        """Decode a raw hash field mapping into *cls*, or ``None`` when it is empty.

        Field names come back as ``str`` whatever Redis handed over, and each
        field decodes on its own, so a bad value is named in the error.  *key*
        names the hash in errors; a field that doesn't
        decode, or fields that don't fit *cls*, raise
        :class:`CacheValidationError`.
        """

        if not mapping:
            return None

        try:
            fields = {
                (field.decode() if isinstance(field, bytes) else field): value for field, value in mapping.items()
            }
        except UnicodeDecodeError as e:
            raise CacheValidationError(f"Hash {key!r} has a field name that isn't UTF-8: {e}") from e

        if cls is bytes:
            return fields

        decoded: dict[str, Any] = {}

        for field, value in fields.items():
            try:
                decoded[field] = msgspec.json.decode(value)
            except msgspec.DecodeError as e:
                raise CacheValidationError(f"Field {field!r} of hash {key!r} doesn't decode: {e}") from e

        try:
            return msgspec.convert(decoded, type=cls)
        except msgspec.DecodeError as e:
            raise CacheValidationError(f"Hash {key!r} doesn't match {self.type_name(cls)}: {e}") from e
