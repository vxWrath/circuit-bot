from typing import Any, get_origin

import msgspec

from .exceptions import CacheValidationError

__all__ = ("CacheCodec",)


class CacheCodec:
    """Turn cache values into bytes and back again.

    ``CacheClient`` handles the Redis I/O and supplies the *cls*; this class
    owns the encoding.  Single values round-trip through :meth:`encode` and
    :meth:`decode`, hash fields through :meth:`encode_hash` and
    :meth:`decode_hash`, and :meth:`validate_cls` rejects an unsupported *cls*
    before any Redis call.

    Reading is msgspec's, and strict: a cached value is held to the shape it
    was cached as, so an entry written by an earlier version of a model is
    refused rather than coerced into fitting the new one.  A decoder is built
    per *cls* and kept, because building one compiles it.

    A struct encodes itself through msgspec, and one inside a list or dict is
    written the same way, so a shape read once and read again only compiles
    once.  Any :class:`msgspec.Struct` will do - inheriting
    :class:`~circuit_bot.core.model.Model` is a convention of the bot's, not a
    requirement of the cache.
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
        """Whether *obj* is a struct - something msgspec knows how to build.

        Inheriting :class:`~circuit_bot.core.model.Model` is a convention the
        bot follows, not something a codec asks for: anything msgspec can build
        is a shape worth caching.
        """

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

        ``bytes``, *scalars*, *containers*, the generic forms of each
        *containers* entry (``dict[str, T]`` for ``dict``, ``list[T]`` for
        ``list``), and :class:`msgspec.Struct` subclasses are always allowed;
        anything else raises ``TypeError``.
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
        """The decoder for *cls*, built the first time it is asked for.

        Building one compiles it, so a shape read once and read again should
        only be compiled once.
        """

        decoder = self._decoders.get(cls)

        if decoder is None:
            decoder = msgspec.json.Decoder(type=cls)
            self._decoders[cls] = decoder

        return decoder

    def encode(self, value: msgspec.Struct | dict[str, Any] | list[Any] | str | int | float | bool | bytes) -> bytes:
        """Encode *value* for storage.

        A model writes itself, ``dict``, ``list``, and JSON scalars are
        JSON-encoded, and ``bytes`` are stored as-is.  Raises ``TypeError`` for
        any other value type.
        """

        if isinstance(value, bytes):
            return value

        if type(value) in (dict, list, str, int, float, bool) or isinstance(value, msgspec.Struct):
            return self._encoder.encode(value)

        raise TypeError(f"Unsupported value type {type(value)!r}")

    def decode(self, data: bytes, cls: type[Any], *, key: str) -> Any:
        """Decode *data* according to *cls*.

        ``bytes`` pass through as-is and ``int`` is parsed with ``int()`` -
        which is how a counter :meth:`~circuit_bot.core.cache.client.CacheClient.incr`
        wrote reads back.  Everything else is read by the codec built for *cls*.
        *key* names the cache entry in error messages, and a payload that no
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

        Each field value is encoded independently, so readers can fetch and
        decode a subset of fields, and a field holding a model is written the
        way that model writes itself.
        """

        fields: dict[str, Any] = (
            msgspec.to_builtins(instance) if isinstance(instance, msgspec.Struct) else instance
        )

        return {field: self._encoder.encode(value) for field, value in fields.items()}

    def decode_hash(self, mapping: dict[str, bytes] | dict[bytes, bytes], cls: type[Any], *, key: str) -> Any:
        """Decode a raw hash field mapping into *cls*.

        A hash is one JSON object taken apart: :meth:`encode_hash` writes each
        field of it on its own, and this reads each one back and assembles them
        into *cls*.  A field is decoded on its own so that one holding bytes
        that aren't JSON is named, rather than failing the whole hash.

        Field names come back as ``str`` whether Redis handed them over as
        ``bytes`` or not.  *key* names the hash in error messages.

        An empty mapping is a miss and returns ``None`` - nothing was cached,
        so there is nothing to validate.  A field that doesn't decode, or
        fields that don't fit *cls*, raise :class:`CacheValidationError`.
        """

        if not mapping:
            return None

        try:
            fields = {
                (field.decode() if isinstance(field, bytes) else field): value
                for field, value in mapping.items()
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
