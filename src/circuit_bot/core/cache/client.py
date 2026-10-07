from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, get_origin, overload

import msgspec
from redis.asyncio import ConnectionPool, RedisError
from redis.asyncio import Redis as AsyncRedis

from ..env import get_env
from ..exception import CircuitException
from ..logging import get_logger

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("CacheClient",)

logger = get_logger("cache")


class CacheError(CircuitException):
    """Raised when a cache operation fails."""


class CacheNotConnected(CacheError):
    """Raised when a cache operation is attempted before connecting."""


class CacheClient:
    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot

        self._redis: AsyncRedis | None = None
        self._encoder = msgspec.json.Encoder()
        self._decoders: dict[type[Any], msgspec.json.Decoder] = {}

    async def connect(self) -> None:
        if self._redis is not None:
            return

        try:
            pool = ConnectionPool.from_url(
                get_env("REDIS_URL"),
                max_connections=10,
                socket_connect_timeout=5.0,
                socket_keepalive=True,
                retry_on_timeout=True,
                decode_responses=False,
            )
            self._redis = AsyncRedis(connection_pool=pool)

            # Verify connection
            await self._redis.ping()

            # Log connection info
            info = await self._redis.info()
            active_connections = info["clients"]["connected_clients"]
            logger.info("Redis pool created with %d connections (max %d)", active_connections, pool.max_connections)

            return
        except (OSError, RedisError) as e:
            raise CacheNotConnected(f"Failed to connect to Redis: {e}") from e

    async def close(self) -> None:
        if self._redis is None:
            return

        await self._redis.aclose()
        self._redis = None

    @property
    def redis(self) -> AsyncRedis:
        if not self._redis:
            raise CacheNotConnected("CacheClient has not been initialized.")
        return self._redis

    @staticmethod
    def key_from_path(*path: Any) -> str:
        """Build a colon-delimited Redis key from path segments.

        Each segment is converted via ``str()``, so integers, snowflakes,
        and enum values all work without manual formatting::

            Cache.key_from_path("sfa", "profile", "player", 789)
            # → "sfa:profile:player:789"
        """
        return ":".join(str(p) for p in path)

    # -- single-key operations (path-based) ---------------------------------

    @overload
    async def get(self, *path: Any, cls: type[bytes]) -> bytes | None: ...

    @overload
    async def get(self, *path: Any, cls: type[int]) -> int | None: ...

    @overload
    async def get[T: msgspec.Struct](self, *path: Any, cls: type[T]) -> T | None: ...

    @overload
    async def get[T](self, *path: Any, cls: type[dict[str, T]]) -> dict[str, T] | None: ...

    @overload
    async def get[T](self, *path: Any, cls: type[list[T]]) -> list[T] | None: ...

    async def get(self, *path: Any, cls: type[Any], **redis_kwargs: Any) -> Any | None:
        """Get and decode a value at *path*.  Returns ``None`` on miss, Redis error,
        or a decode failure (stale/shape-changed cached value).

        *cls* controls decoding:

        - ``bytes`` → raw bytes, no decoding
        - ``dict`` or ``list`` → decoded via ``msgspec.json.Decoder``
        - ``list[T]`` / ``dict[str, T]`` → decoded via a cached ``msgspec.json.Decoder``
        - ``msgspec.Struct`` subclass → decoded via a cached ``msgspec.json.Decoder``
        """
        key = self.key_from_path(*path)

        try:
            data = await self.redis.get(key, **redis_kwargs)
        except RedisError:
            logger.warning("Redis GET failed for key %r", key, exc_info=True)
            return None

        if data is None or cls is bytes:
            return data

        if cls is int:
            return int(data)

        origin = get_origin(cls)
        if (
            cls is dict
            or cls is list
            or origin is dict
            or origin is list
            or (isinstance(cls, type) and issubclass(cls, msgspec.Struct))
        ):
            decoder = self._decoders.get(cls)
            if decoder is None:
                decoder = msgspec.json.Decoder(type=cls, strict=False)
                self._decoders[cls] = decoder

            try:
                return decoder.decode(data)
            except msgspec.ValidationError:
                # Stale or shape-changed cached values degrade to a miss and
                # refill from the source.
                logger.warning("Cache GET decode failed for key %r", key, exc_info=True)
                return None

        raise TypeError(f"Unsupported cls {cls!r}")

    async def set(
        self,
        *path: Any,
        value: msgspec.Struct | dict[str, Any] | list[Any] | bytes,
        **redis_kwargs: Any,
    ) -> bool:
        """Set a value at *path* with ex in seconds.  Returns ``False`` on Redis error.

        *value* is auto-encoded: ``msgspec.Struct`` → ``msgspec.json``,
        ``dict`` or ``list`` → ``msgspec.json.Encoder``, ``bytes`` → stored as-is.
        """
        key = self.key_from_path(*path)
        value_type = type(value)

        if value_type is bytes:
            data = value
        elif value_type is dict or value_type is list or isinstance(value, msgspec.Struct):
            data = self._encoder.encode(value)
        else:
            raise TypeError(f"Unsupported value type {type(value)!r}")

        try:
            return await self.redis.set(key, data, ex=redis_kwargs.pop("ex", 300), **redis_kwargs)  # type: ignore[arg-type]
        except RedisError:
            logger.warning("Redis SET failed for key %r", key, exc_info=True)
            return False

    async def exists(self, *path: Any) -> bool:
        """Return ``True`` if a key exists at *path*.  Returns ``False`` on Redis error."""

        if not path:
            return False

        key = self.key_from_path(*path)

        try:
            count = await self.redis.exists(key)
            return count > 0
        except RedisError:
            logger.warning("Redis EXISTS failed for key %r", key, exc_info=True)
            return False

    async def expire(self, *path: Any, ex: int, **redis_kwargs: Any) -> bool:
        """Set or update the expiry (in seconds) on an existing key.

        Returns ``True`` if the timeout was set, ``False`` if the key doesn't
        exist or on Redis error.
        """

        key = self.key_from_path(*path)

        try:
            return await self.redis.expire(key, ex, **redis_kwargs)
        except RedisError:
            logger.warning("Redis EXPIRE failed for key %r", key, exc_info=True)
            return False

    async def incr(self, *path: Any, amount: int = 1) -> int | None:
        """Atomically increment a counter at *path* by *amount*.

        Returns the new value, or ``None`` on Redis error.  If the key doesn't
        exist it is set to 0 before incrementing.
        """

        key = self.key_from_path(*path)

        try:
            return await self.redis.incrby(key, amount)
        except RedisError:
            logger.warning("Redis INCRBY failed for key %r", key, exc_info=True)
            return None

    # -- hash operations (path-based) ---------------------------------------

    @overload
    async def hash_get(
        self, *path: Any, cls: type[bytes], fields: Iterable[str]
    ) -> tuple[dict[str, bytes] | None, set[str]]: ...

    @overload
    async def hash_get[T: msgspec.Struct](
        self, *path: Any, cls: type[T], fields: Iterable[str]
    ) -> tuple[T | None, set[str]]: ...

    @overload
    async def hash_get[D: dict[str, Any]](
        self, *path: Any, cls: type[D], fields: Iterable[str]
    ) -> tuple[D | None, set[str]]: ...

    async def hash_get(self, *path: Any, cls: type[Any], fields: Iterable[str]) -> tuple[Any | None, set[str]]:
        """Get one or more fields from a hash at *path*.

        *cls* controls how field values are decoded:

        - ``dict[str, bytes]`` → raw bytes values, no decoding
        - ``dict[str, Any]`` → each value decoded via ``msgspec.json.Decoder``
        - ``list[Any]`` → each value decoded via ``msgspec.json.Decoder``
        - ``msgspec.Struct`` → field mapping decoded into the struct type

        Returns ``(cls or None, missing fields)``
        """

        key = self.key_from_path(*path)

        ordered_fields = sorted(fields)
        try:
            hmget: list[bytes | None] = await self.redis.hmget(key, ordered_fields)  # type: ignore
        except RedisError:
            logger.warning("Redis HMGET failed for key %r", key, exc_info=True)
            return (None, set(fields))

        try:
            items = zip(ordered_fields, hmget, strict=True)
        except ValueError:
            logger.warning(
                "Redis HMGET field count mismatch for key %r. Requested %d, got %d",
                key,
                len(ordered_fields),
                len(hmget),
            )
            return (None, set(fields))

        mapping: dict[str, Any] = {}
        missing: set[str] = set()

        for field, value in items:
            field_name = field.decode() if isinstance(field, bytes) else field

            if value is None:
                missing.add(field_name)
                continue

            elif cls is bytes or cls is dict or cls is list or issubclass(cls, msgspec.Struct):
                mapping[field_name] = value

            else:
                raise TypeError(f"Unsupported cls {cls!r}")

        if cls is bytes or cls is dict:
            return (mapping, missing)

        return (msgspec.convert(mapping, type=cls, strict=False), missing)

    @overload
    async def hash_getall(self, *path: Any, cls: type[bytes]) -> dict[str, bytes] | None: ...

    @overload
    async def hash_getall[T: msgspec.Struct](self, *path: Any, cls: type[T]) -> T | None: ...

    @overload
    async def hash_getall[D: dict[str, Any]](self, *path: Any, cls: type[D]) -> D | None: ...

    async def hash_getall(self, *path: Any, cls: type[Any]) -> Any | None:
        """Get all fields from a hash at *path*.

        *cls* controls how field values are decoded:

        - ``dict[str, bytes]`` → raw bytes values, no decoding
        - ``dict[str, Any]`` → each value decoded via ``msgspec.json.Decoder``
        - ``list[Any]`` → each value decoded via ``msgspec.json.Decoder``
        - ``msgspec.Struct`` → field mapping decoded into the struct type

        Returns ``None`` if the key doesn't exist or on Redis error.
        """

        key = self.key_from_path(*path)

        try:
            hgetall: dict[bytes, bytes] = await self.redis.hgetall(key)  # type: ignore
        except RedisError:
            logger.warning("Redis HGETALL failed for key %r", key, exc_info=True)
            return None

        # Redis auto-deletes a hash when its last field is removed,
        # so an empty hgetall always means the key doesn't exist.
        if not hgetall:
            return None

        mapping: dict[str, Any] = {}

        for field, value in hgetall.items():
            if cls is bytes or cls is dict or cls is list or issubclass(cls, msgspec.Struct):
                mapping[field.decode()] = value

            else:
                raise TypeError(f"Unsupported cls {cls!r}")

        if cls is bytes or cls is dict:
            return mapping

        return msgspec.convert(mapping, type=cls, strict=False)

    async def hash_set(
        self,
        *path: Any,
        instance: msgspec.Struct | dict[str, Any],
        **redis_kwargs: Any,
    ) -> int:
        """Set all fields of a hash from a ``dict`` or ``msgspec.Struct``.

        Returns the number of fields added, or 0 on Redis error.
        """

        key = self.key_from_path(*path)
        encoded: dict[str, bytes]

        if isinstance(instance, msgspec.Struct):
            data: dict[str, Any] = msgspec.to_builtins(instance)
            encoded = {k: self._encoder.encode(v) for k, v in data.items()}
        else:
            encoded = {k: self._encoder.encode(v) for k, v in instance.items()}

        if not encoded:
            return 0

        try:
            async with self.redis.pipeline() as pipe:
                await pipe.hset(key, mapping=encoded)  # type: ignore
                await pipe.expire(key, time=redis_kwargs.pop("ex", 300), **redis_kwargs)

                commands = await pipe.execute()
                return commands[0]

        except RedisError:
            logger.warning("Redis HSET failed for key %r", key, exc_info=True)
            return 0

    async def hash_delete(self, *path: Any, fields: Iterable[str]) -> int:
        """Delete one or more fields from a hash at *path*.

        Returns the number of fields removed, or 0 on Redis error.
        """

        key = self.key_from_path(*path)

        try:
            return await self.redis.hdel(key, *fields)
        except RedisError:
            logger.warning("Redis HDEL failed for key %r", key, exc_info=True)
            return 0

    # -- multi-key operations (raw key strings) -----------------------------

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys.  Returns count deleted, or 0 on Redis error.

        Keys are pre-built strings.  Use :meth:`key_from_path` to construct them::

            await cache.delete(
                cache.key_from_path("sfa", "row", "players", "789"),
                cache.key_from_path("sfa", "profile", "player", "789"),
            )
        """

        if not keys:
            return 0

        try:
            return await self.redis.delete(*keys)
        except RedisError:
            logger.warning("Redis DELETE failed for keys %r", keys, exc_info=True)
            return 0

    async def keys(self, pattern: str) -> list[str]:
        """Return keys matching *pattern* via ``SCAN``.  Returns empty list on Redis error.

        ``SCAN`` iterates the keyspace in small batches so it won't block
        the server.  Still prefer well-scoped patterns like
        ``sfa:profile:player:*`` - never ``*`` in production.
        """

        try:
            result: list[str] = []
            cursor = 0
            while True:
                scan: tuple[int, list[bytes]] = await self.redis.scan(cursor, match=pattern)  # type: ignore
                cursor, batch = scan

                result.extend(k.decode() for k in batch)

                if cursor == 0:
                    break

            return result
        except RedisError:
            logger.warning("Redis SCAN failed for pattern %r", pattern, exc_info=True)
            return []

    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching *pattern*.  Returns count deleted."""

        keys = await self.keys(pattern)
        if not keys:
            return 0

        return await self.delete(*keys)
