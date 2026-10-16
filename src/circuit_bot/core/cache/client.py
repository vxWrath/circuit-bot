import random
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, overload

import msgspec
from redis.asyncio import ConnectionPool, RedisError
from redis.asyncio import Redis as AsyncRedis

from ...config import get_redis_settings
from ..logging import get_logger
from .codec import CacheCodec
from .exceptions import CacheError, CacheNotConnected

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("CacheClient",)

logger = get_logger("cache")


class CacheClient:
    """Async Redis cache, available on the bot as ``bot.cache``.

    Keys are built from *path* segments rather than hand-formatted strings
    (:meth:`key_from_path` defines the layout), so every call site shares one
    key shape::

        await cache.set("user", 1234, value=user, ex=60)
        user = await cache.get("user", 1234, cls=User)  # → User | None

    Entries written by :meth:`set` and :meth:`hash_set` expire after ~300
    seconds by default, jittered by ±10% so a bulk write doesn't expire - and
    refill from the source of truth - all at once.  Pass ``ex`` for a fixed
    TTL, or ``ex=None`` to keep an entry until it's deleted.

    Misses are not errors: nothing found in Redis comes back as ``None``,
    ``False``, ``0``, or an empty list, and that is the only thing that means
    "not cached" - apart from :meth:`get_or_absent`, whose ``absent`` flag is
    how a caller tells "not cached" from a :meth:`set_absent` marker saying the
    source had nothing.  A cached value that can't be decoded into the requested *cls*
    raises :class:`CacheValidationError`, and a Redis failure raises
    :class:`CacheError`, so the caller can always tell the three apart.
    Calling before :meth:`connect` raises :class:`CacheNotConnected`, and an
    unsupported *cls*, value type, or empty path raises ``TypeError`` - the
    last two signal a bug, not a cache miss.
    """

    DEFAULT_TTL = 300  # seconds, before jitter
    TTL_JITTER = 0.1  # fraction of DEFAULT_TTL applied in either direction

    # The options Redis EXPIRE takes, which are the only ones a hash write can
    # pass on: everything else a caller had in mind belongs to another command.
    EXPIRE_OPTIONS = frozenset({"nx", "xx", "gt", "lt"})

    # What set_absent() stores, and what get_or_absent() reads back as "the
    # source was asked and had nothing".  A NUL can't begin JSON, so it can't
    # collide with anything the codec wrote - and set() refuses it outright.
    ABSENT = b"\x00"

    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot

        self._redis: AsyncRedis | None = None
        self._codec = CacheCodec()

    async def connect(self) -> None:
        """Create the client, opening a connection to Redis.

        Reads the connection settings from the ``REDIS_*`` environment variables
        and is a no-op if the client already exists, so it is safe to call more
        than once.  Raises :class:`CacheNotConnected` if Redis can't be reached, or
        if a setting is missing or malformed.
        """

        if self._redis is not None:
            return

        try:
            # Inside the block deliberately: get_env raises OSError for a missing
            # variable, and that is one of the failures this translates.
            settings = get_redis_settings()

            pool = ConnectionPool(
                host=settings.host,
                port=settings.port,
                password=settings.password,
                db=settings.db,
                max_connections=10,
                socket_connect_timeout=5.0,
                socket_keepalive=True,
                retry_on_timeout=True,
                decode_responses=False,
            )
            self._redis = AsyncRedis(connection_pool=pool)

            # Verify the connection before handing the client out
            await self._redis.ping()

            logger.info(
                "Redis connected to %s:%d/%d (pool allows %d connections)",
                settings.host,
                settings.port,
                settings.db,
                pool.max_connections,
            )
        except (OSError, RedisError, ValueError) as e:
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
        """Join *path* segments into a colon-delimited Redis key.

        Each segment is converted with ``str()``, so ints, strings, and enums
        can be mixed without manual formatting::

            cache.key_from_path("user", 1234, "profile")
            # → "user:1234:profile"

        At least one segment is required, since an empty path would name the
        key ``""`` - no call means that, so it's a bug rather than a lookup.
        Every path-based method builds its key here, which is what makes this
        the one place the check has to be.
        """

        if not path:
            raise TypeError("a key needs at least one path segment")

        return ":".join(str(p) for p in path)

    # -- internals ----------------------------------------------------------

    @classmethod
    def resolve_ttl(cls, redis_kwargs: dict[str, Any]) -> int | None:
        """Pop ``ex`` from *redis_kwargs*, falling back to a jittered default TTL.

        An explicit ``ex`` is used as-is, and ``ex=None`` means the entry never
        expires.  Only the default is jittered, so keys written in bulk don't
        all expire - and refill from the source - at the same moment.
        """

        if "ex" in redis_kwargs:
            return redis_kwargs.pop("ex")

        jitter = random.uniform(1 - cls.TTL_JITTER, 1 + cls.TTL_JITTER)
        return int(cls.DEFAULT_TTL * jitter)

    # -- single-key operations (path-based) ---------------------------------

    @overload
    async def get(self, *path: Any, cls: type[bytes]) -> bytes | None: ...

    @overload
    async def get(self, *path: Any, cls: type[int]) -> int | None: ...

    @overload
    async def get(self, *path: Any, cls: type[str]) -> str | None: ...

    @overload
    async def get(self, *path: Any, cls: type[float]) -> float | None: ...

    @overload
    async def get(self, *path: Any, cls: type[bool]) -> bool | None: ...

    @overload
    async def get[T: msgspec.Struct](self, *path: Any, cls: type[T]) -> T | None: ...

    @overload
    async def get[T](self, *path: Any, cls: type[dict[str, T]]) -> dict[str, T] | None: ...

    @overload
    async def get[T](self, *path: Any, cls: type[list[T]]) -> list[T] | None: ...

    async def get(self, *path: Any, cls: type[Any], **redis_kwargs: Any) -> Any | None:
        """Get the value at *path*, decoded according to *cls*.

        Returns ``None`` only on a miss.  A cached payload that no longer
        decodes into *cls* raises :class:`CacheValidationError` instead, so a
        miss and a stale entry are never confused.

        *cls* selects how the stored bytes are turned back into a value:

        - ``bytes`` → returned as-is
        - ``int`` → parsed as an integer
        - ``str``, ``float``, ``bool`` → JSON-decoded scalars
        - ``dict``, ``list``, ``list[T]``, ``dict[str, T]`` → JSON-decoded
        - ``msgspec.Struct`` subclass → JSON-decoded into that struct

        Example::

            user = await cache.get("user", 1234, cls=User)
            logins = await cache.get("stats", "logins", cls=int)

        Raises ``TypeError`` for an unsupported *cls*, before touching Redis,
        and :class:`CacheError` if Redis fails.
        """

        self._codec.validate_cls(cls, scalars=(int, str, float, bool), containers=(dict, list))
        key = self.key_from_path(*path)

        try:
            data: bytes | None = await self.redis.get(key, **redis_kwargs)  # type: ignore
        except RedisError as e:
            raise CacheError(f"Redis GET failed for key {key!r}") from e

        if data is None:
            return None

        return self._codec.decode(data, cls, key=key)

    async def set(
        self,
        *path: Any,
        value: msgspec.Struct | dict[str, Any] | list[Any] | str | int | float | bool | bytes,
        **redis_kwargs: Any,
    ) -> None:
        """Store *value* at *path*, expiring after ~300 seconds by default.

        *value* is encoded automatically: ``msgspec.Struct``, ``dict``,
        ``list``, and JSON scalars are JSON-encoded, while ``bytes`` are stored
        as-is.  Pass ``ex`` (in seconds) for a fixed TTL, or ``ex=None`` to
        store the entry with no expiry.  Raises :class:`CacheError` if Redis
        fails::

            await cache.set("user", 1234, value=user)
            await cache.set("stats", "logins", value=count, ex=60)
        """
        key = self.key_from_path(*path)
        data = self._codec.encode(value)

        if data == self.ABSENT:
            raise TypeError(f"{self.ABSENT!r} is this cache's own marker for an absent entry - see set_absent()")

        ex = self.resolve_ttl(redis_kwargs)

        try:
            await self.redis.set(key, data, ex=ex, **redis_kwargs)
        except RedisError as e:
            raise CacheError(f"Redis SET failed for key {key!r}") from e

    async def set_absent(self, *path: Any, **redis_kwargs: Any) -> None:
        """Remember at *path* that the source was asked and had nothing.

        Negative caching is worth the same key a value would take: the marker
        is one byte (see :attr:`ABSENT`), written where the value would have
        gone, so asking again costs the one round trip
        :meth:`get_or_absent` spends rather than a second lookup of its own.
        It expires like any other entry - ~300 seconds by default, ``ex`` for a
        fixed TTL, ``ex=None`` until it's deleted.  Raises :class:`CacheError`
        if Redis fails.
        """

        key = self.key_from_path(*path)
        ex = self.resolve_ttl(redis_kwargs)

        try:
            await self.redis.set(key, self.ABSENT, ex=ex, **redis_kwargs)
        except RedisError as e:
            raise CacheError(f"Redis SET failed for key {key!r}") from e

    @overload
    async def get_or_absent(self, *path: Any, cls: type[bytes]) -> tuple[bytes | None, bool]: ...

    @overload
    async def get_or_absent(self, *path: Any, cls: type[int]) -> tuple[int | None, bool]: ...

    @overload
    async def get_or_absent(self, *path: Any, cls: type[str]) -> tuple[str | None, bool]: ...

    @overload
    async def get_or_absent(self, *path: Any, cls: type[float]) -> tuple[float | None, bool]: ...

    @overload
    async def get_or_absent(self, *path: Any, cls: type[bool]) -> tuple[bool | None, bool]: ...

    @overload
    async def get_or_absent[T: msgspec.Struct](self, *path: Any, cls: type[T]) -> tuple[T | None, bool]: ...

    @overload
    async def get_or_absent[T](self, *path: Any, cls: type[dict[str, T]]) -> tuple[dict[str, T] | None, bool]: ...

    @overload
    async def get_or_absent[T](self, *path: Any, cls: type[list[T]]) -> tuple[list[T] | None, bool]: ...

    async def get_or_absent(self, *path: Any, cls: type[Any], **redis_kwargs: Any) -> tuple[Any | None, bool]:
        """Get the value at *path*, or find out that the source had nothing.

        Returns ``(value, absent)``, which keeps three answers apart in one
        read: ``(value, False)`` is a hit, ``(None, True)`` is an entry
        :meth:`set_absent` wrote - the source was asked and had nothing - and
        ``(None, False)`` is a miss, where nothing is known yet and the source
        still has to be asked.  A plain ``None`` can't tell the middle from the
        last, which is why the answer is stored rather than inferred.

        *cls* selects how the stored bytes are read, exactly as it does for
        :meth:`get` - and ``None`` is never a cached value, so it's free to
        mean "nothing" here.

        Raises ``TypeError`` for an unsupported *cls*, before touching Redis,
        and :class:`CacheError` if Redis fails.
        """

        self._codec.validate_cls(cls, scalars=(int, str, float, bool), containers=(dict, list))
        key = self.key_from_path(*path)

        try:
            data: bytes | None = await self.redis.get(key, **redis_kwargs)  # type: ignore
        except RedisError as e:
            raise CacheError(f"Redis GET failed for key {key!r}") from e

        if data is None:
            return None, False

        if data == self.ABSENT:
            return None, True

        return self._codec.decode(data, cls, key=key), False

    async def exists(self, *path: Any) -> bool:
        """Return ``True`` if a key exists at *path*, ``False`` otherwise.

        Raises :class:`CacheError` if Redis fails.
        """

        key = self.key_from_path(*path)

        try:
            count = await self.redis.exists(key)
        except RedisError as e:
            raise CacheError(f"Redis EXISTS failed for key {key!r}") from e

        return count > 0

    async def expire(self, *path: Any, ex: int, **redis_kwargs: Any) -> bool:
        """Set or update the expiry (in seconds) on an existing key.

        Returns ``True`` if the timeout was set, ``False`` if the key doesn't
        exist.  Raises :class:`CacheError` if Redis fails.
        """

        key = self.key_from_path(*path)

        try:
            return await self.redis.expire(key, ex, **redis_kwargs)
        except RedisError as e:
            raise CacheError(f"Redis EXPIRE failed for key {key!r}") from e

    async def incr(self, *path: Any, amount: int = 1) -> int:
        """Atomically increment a counter at *path* by *amount* and return the new value.

        If the key doesn't exist it is set to 0 before incrementing.  Raises
        :class:`CacheError` if Redis fails.
        """

        key = self.key_from_path(*path)

        try:
            return await self.redis.incrby(key, amount)
        except RedisError as e:
            raise CacheError(f"Redis INCRBY failed for key {key!r}") from e

    # -- hash operations (path-based) ---------------------------------------

    @overload
    async def hash_get(
        self, *path: Any, cls: type[bytes], fields: Iterable[str]
    ) -> tuple[dict[str, bytes] | None, set[str]]: ...

    @overload
    async def hash_get[D](
        self, *path: Any, cls: type[dict[str, D]], fields: Iterable[str]
    ) -> tuple[dict[str, D] | None, set[str]]: ...

    @overload
    async def hash_get[T: msgspec.Struct](
        self, *path: Any, cls: type[T], fields: Iterable[str]
    ) -> tuple[T | None, set[str]]: ...

    async def hash_get(self, *path: Any, cls: type[Any], fields: Iterable[str]) -> tuple[Any | None, set[str]]:
        """Get the requested *fields* from the hash at *path*.

        *cls* selects how field values are returned:

        - ``bytes`` → a ``dict`` of raw ``bytes`` values
        - ``dict``, ``dict[str, T]`` → each value JSON-decoded into a ``dict``
        - ``msgspec.Struct`` subclass → each value JSON-decoded into that struct

        Returns ``(value, missing)``, where *missing* is the set of requested
        fields that weren't in the hash, so callers can refill just those.
        *value* is ``None`` when the hash holds none of the requested fields,
        whatever *cls* is - that is a miss, not an error.  A cached field that
        no longer decodes, or fields that can't be assembled into *cls* - e.g.
        a required field wasn't cached or requested - raises
        :class:`CacheValidationError` rather than joining *missing*::

            user, missing = await cache.hash_get("user", 1234, cls=User, fields=("name", "level"))
            if missing:
                ...  # refill the fields that weren't cached

        Raises ``TypeError`` if *fields* is empty and :class:`CacheError` if
        Redis fails.
        """

        self._codec.validate_cls(cls, containers=(dict,))

        ordered_fields = sorted(fields)

        if not ordered_fields:
            raise TypeError("hash_get() requires at least one field")

        key = self.key_from_path(*path)

        try:
            hmget: list[bytes | None] = await self.redis.hmget(key, ordered_fields)  # type: ignore
        except RedisError as e:
            raise CacheError(f"Redis HMGET failed for key {key!r}") from e

        if len(hmget) != len(ordered_fields):
            raise CacheError(
                f"Redis HMGET on key {key!r} returned {len(hmget)} values for {len(ordered_fields)} fields"
            )

        mapping: dict[str, bytes] = {}
        missing: set[str] = set()

        for field, value in zip(ordered_fields, hmget, strict=True):
            if value is None:
                missing.add(field)
            else:
                mapping[field] = value

        return (self._codec.decode_hash(mapping, cls, key=key), missing)

    @overload
    async def hash_getall(self, *path: Any, cls: type[bytes]) -> dict[str, bytes] | None: ...

    @overload
    async def hash_getall[D](self, *path: Any, cls: type[dict[str, D]]) -> dict[str, D] | None: ...

    @overload
    async def hash_getall[T: msgspec.Struct](self, *path: Any, cls: type[T]) -> T | None: ...

    async def hash_getall(self, *path: Any, cls: type[Any]) -> Any | None:
        """Get every field of the hash at *path*.

        *cls* selects how field values are returned:

        - ``bytes`` → a ``dict`` of raw ``bytes`` values
        - ``dict``, ``dict[str, T]`` → each value JSON-decoded into a ``dict``
        - ``msgspec.Struct`` subclass → each value JSON-decoded into that struct

        Returns ``None`` if the hash doesn't exist - Redis deletes a hash once
        its last field is removed.  A field that no longer decodes, or a hash
        that can't be assembled into *cls*, raises
        :class:`CacheValidationError`.  Raises :class:`CacheError` if Redis
        fails::

            user = await cache.hash_getall("user", 1234, cls=User)
        """

        self._codec.validate_cls(cls, containers=(dict,))

        key = self.key_from_path(*path)

        try:
            hgetall: dict[bytes, bytes] = await self.redis.hgetall(key)  # type: ignore
        except RedisError as e:
            raise CacheError(f"Redis HGETALL failed for key {key!r}") from e

        # Redis auto-deletes a hash when its last field is removed,
        # so an empty hgetall always means the key doesn't exist.
        if not hgetall:
            return None

        return self._codec.decode_hash(hgetall, cls, key=key)

    async def hash_set(
        self,
        *path: Any,
        instance: msgspec.Struct | dict[str, Any],
        **redis_kwargs: Any,
    ) -> int:
        """Write all fields of *instance* to the hash at *path*.

        *instance* is a ``msgspec.Struct`` or ``dict`` whose fields are
        JSON-encoded.  The hash expires after ~300 seconds by default; pass
        ``ex`` for a fixed TTL or ``ex=None`` to keep it until it's deleted.
        The flags ``EXPIRE`` takes - ``nx``, ``xx``, ``gt``, ``lt`` - are
        passed on with it, and any other expiry option is a ``TypeError``
        rather than a surprise from Redis several frames down.

        Returns the number of new fields added, and raises :class:`CacheError`
        if Redis fails::

            await cache.hash_set("user", 1234, instance=user)
            await cache.hash_set("user", 1234, instance={"level": 12}, ex=60)
        """

        key = self.key_from_path(*path)
        ex = self.resolve_ttl(redis_kwargs)

        if ex is None and redis_kwargs:
            raise TypeError(f"Expiry options {sorted(redis_kwargs)} are meaningless with ex=None")

        # The leftovers ride along to EXPIRE, so they have to be options EXPIRE
        # takes: ``px`` or ``exat`` would otherwise only be refused by Redis.
        unknown = sorted(set(redis_kwargs) - self.EXPIRE_OPTIONS)

        if unknown:
            raise TypeError(f"hash_set() takes ex, and {sorted(self.EXPIRE_OPTIONS)} alongside it - not {unknown}")

        encoded = self._codec.encode_hash(instance)

        if not encoded:
            return 0

        try:
            async with self.redis.pipeline() as pipe:
                await pipe.hset(key, mapping=encoded)  # type: ignore

                if ex is not None:
                    await pipe.expire(key, time=ex, **redis_kwargs)

                commands = await pipe.execute()
                return commands[0]

        except RedisError as e:
            raise CacheError(f"Redis HSET failed for key {key!r}") from e

    async def hash_delete(self, *path: Any, fields: Iterable[str]) -> int:
        """Delete one or more fields from a hash at *path*.

        Returns the number of fields removed.  Raises ``TypeError`` if
        *fields* is empty and :class:`CacheError` if Redis fails.
        """

        fields = tuple(fields)

        if not fields:
            raise TypeError("hash_delete() requires at least one field")

        key = self.key_from_path(*path)

        try:
            return await self.redis.hdel(key, *fields)
        except RedisError as e:
            raise CacheError(f"Redis HDEL failed for key {key!r}") from e

    # -- multi-key operations (raw key strings) -----------------------------

    async def delete(self, *keys: str) -> int:
        """Delete one or more complete keys.  Returns the count deleted.

        Unlike the path-based methods, *keys* are full key strings - build
        them with :meth:`key_from_path` when deleting keys of different
        shapes in one call.  Raises :class:`CacheError` if Redis fails::

            await cache.delete(
                cache.key_from_path("user", 1234, "profile"),
                cache.key_from_path("user", 1234, "scores"),
            )
        """

        if not keys:
            return 0

        try:
            return await self.redis.delete(*keys)
        except RedisError as e:
            raise CacheError(f"Redis DELETE failed for keys {keys!r}") from e

    async def keys(self, pattern: str) -> list[str]:
        """Return every key matching *pattern* via ``SCAN``.

        ``SCAN`` walks the keyspace in small batches so it won't block the
        server.  Keep patterns as narrow as possible - never ``*`` in
        production.  An empty list means nothing matched; raises
        :class:`CacheError` if Redis fails::

            for key in await cache.keys("user:1234:*"):
                ...
        """

        try:
            result: list[str] = []
            cursor = 0
            while True:
                scan: tuple[int, list[bytes]] = await self.redis.scan(cursor, match=pattern, count=25)  # type: ignore
                cursor, batch = scan

                result.extend(k.decode() for k in batch)

                if cursor == 0:
                    break

            return result
        except RedisError as e:
            raise CacheError(f"Redis SCAN failed for pattern {pattern!r}") from e

    async def delete_pattern(self, pattern: str) -> int:
        """Delete every key matching *pattern*.  Returns the count deleted.

        Shorthand for :meth:`keys` followed by :meth:`delete`, so the same
        warning about overly broad patterns applies.  Raises
        :class:`CacheError` if Redis fails::

            removed = await cache.delete_pattern("user:1234:*")
        """

        keys = await self.keys(pattern)
        if not keys:
            return 0

        return await self.delete(*keys)
