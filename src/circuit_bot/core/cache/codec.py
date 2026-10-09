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

    Decoders are cached per *cls*.
    """

    def __init__(self) -> None:
        self._encoder = msgspec.json.Encoder()
        self._decoders: dict[type[Any], msgspec.json.Decoder] = {}

    @staticmethod
    def _type_name(as_type: type[Any]) -> str:
        """Render *as_type* for error messages: ``User``, ``dict[str, str]``."""

        return as_type.__name__ if isinstance(as_type, type) else str(as_type)

    @staticmethod
    def validate_cls(
        as_type: type[Any],
        *,
        scalars: tuple[type[Any], ...] = (),
        containers: tuple[type[Any], ...] = (),
    ) -> None:
        """Reject an unsupported *cls*.

        ``bytes``, *scalars*, *containers*, the generic forms of each
        *containers* entry (``dict[str, T]`` for ``dict``, ``list[T]`` for
        ``list``), and ``msgspec.Struct`` subclasses are always allowed;
        anything else raises ``TypeError``.
        """

        if as_type is bytes or as_type in scalars:
            return

        origin = get_origin(as_type)
        if any(as_type is c or origin is c for c in containers):
            return

        if isinstance(as_type, type) and issubclass(as_type, msgspec.Struct):
            return

        raise TypeError(f"Unsupported cls {as_type!r}")

    def encode(self, value: msgspec.Struct | dict[str, Any] | list[Any] | str | int | float | bool | bytes) -> bytes:
        """Encode *value* for storage.

        ``msgspec.Struct``, ``dict``, ``list``, and JSON scalars are
        JSON-encoded, while ``bytes`` are stored as-is.  Raises ``TypeError``
        for any other value type.
        """

        if isinstance(value, bytes):
            return value

        if type(value) in (dict, list, str, int, float, bool) or isinstance(value, msgspec.Struct):
            return self._encoder.encode(value)

        raise TypeError(f"Unsupported value type {type(value)!r}")

    def decode(self, data: bytes, cls: type[Any], *, key: str) -> Any:
        """Decode *data* according to *cls*.

        ``bytes`` pass through as-is and ``int`` is parsed with ``int()``;
        everything else is JSON-decoded through a decoder cached per *cls*.
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

        decoder = self._decoders.get(cls)

        if decoder is None:
            decoder = msgspec.json.Decoder(type=cls, strict=False)
            self._decoders[cls] = decoder

        try:
            return decoder.decode(data)
        except msgspec.DecodeError as e:
            raise CacheValidationError(f"Cached value at {key!r} doesn't match {self._type_name(cls)}: {e}") from e

    def encode_hash(self, instance: msgspec.Struct | dict[str, Any]) -> dict[str, bytes]:
        """JSON-encode every field of *instance* on its own.

        Each field value is encoded independently, so readers can fetch and
        decode a subset of fields.
        """

        if isinstance(instance, msgspec.Struct):
            data: dict[str, Any] = msgspec.to_builtins(instance)
            return {k: self._encoder.encode(v) for k, v in data.items()}

        return {k: self._encoder.encode(v) for k, v in instance.items()}

    def decode_hash(self, mapping: dict[bytes, bytes] | dict[str, bytes], cls: type[Any], *, key: str) -> Any:
        """Decode a raw hash field mapping into *cls*.

        Each field value is JSON-decoded on its own, then the fields are
        assembled into *cls*.  *key* names the hash in error messages.  A
        field that doesn't decode or assemble raises
        :class:`CacheValidationError`, while an empty mapping is a miss and
        returns ``None`` - nothing was cached, so there is nothing to
        validate.
        """

        if not mapping:
            return None

        if cls is bytes:
            return mapping

        decoded: dict[str, Any] = {}
        field_is_bytes = next(iter(mapping)) is bytes

        if field_is_bytes:
            for field, value in mapping.items():
                try:
                    decoded[field.decode()] = msgspec.json.decode(value)  # type: ignore
                except msgspec.DecodeError as e:
                    raise CacheValidationError(f"Field {field!r} of hash {key!r} doesn't decode: {e}") from e
        else:
            for field, value in mapping.items():
                try:
                    decoded[field] = msgspec.json.decode(value)  # type: ignore
                except msgspec.DecodeError as e:
                    raise CacheValidationError(f"Field {field!r} of hash {key!r} doesn't decode: {e}") from e

        if cls is dict:
            return decoded

        try:
            return msgspec.convert(decoded, type=cls, strict=False)
        except msgspec.ValidationError as e:
            raise CacheValidationError(f"Hash {key!r} doesn't match {self._type_name(cls)}: {e}") from e
