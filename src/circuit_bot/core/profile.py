from collections.abc import Callable, Coroutine
from typing import Any

from .singleflight import SingleFlight

__all__ = ("Profile",)


class Profile[S]:
    """A view over several sources that fills itself in on demand.

    Unlike a model, a profile is not a snapshot: it starts empty, fetches each
    part the first time it's asked for, and keeps it.  A command pays only for
    what it renders, and because the clients underneath cache their own reads,
    asking for a part twice costs nothing.

    An empty part is kept too - ``None`` is an answer, and re-asking would
    repeat the lookup that already found nothing.  A loader that *raises* keeps
    nothing, so the next call tries again, and two coroutines asking for the
    same part at once are answered by one call rather than one each - the
    command that renders four parts at once pays for four lookups, not for
    every ask.

    A profile mirrors its service: every service method that takes the
    identifier the profile was opened with gets a counterpart here that drops
    it.  Reads fill parts; writes go through the service and then refresh the
    profile, so a caller never reads a part the write has invalidated::

        player = await profile.link_roblox(roblox_id)
        assert (await profile.player()).roblox_id == roblox_id

    Parts stay within one module's subject - anything belonging to another
    module goes through that module's service.
    """

    def __init__(self, *, service: S) -> None:
        self.service = service
        self._parts: dict[Any, Any] = {}

        # Parts are fetched through this, so two coroutines wanting the same one
        # at the same moment make one lookup of it - see SingleFlight.
        self._in_flight = SingleFlight()

    async def ensure_part[T: Any](
        self,
        key: Any,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Call ``func(*args, **kwargs)`` once per *key* and keep what it returns.

        A call that raises keeps nothing, so the next ask tries again; asks that
        arrive while one is in flight wait on it rather than starting their own.
        """

        if key in self._parts:
            return self._parts[key]

        value = await self._in_flight.run(key, func, *args, **kwargs)
        self._parts[key] = value

        return value

    def set_part(self, key: Any, value: Any) -> None:
        """Keep *value* as the answer for *key* without calling anything."""
        self._parts[key] = value

    def invalidate_parts(self, *keys: Any) -> None:
        """Forget parts, so the next access fetches them again.

        Called after a write.  With no keys every part is dropped; name keys to
        keep the parts a write couldn't have changed.
        """

        if not keys:
            self._parts.clear()
            return

        for key in keys:
            self._parts.pop(key, None)
