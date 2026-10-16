from collections.abc import Callable, Coroutine
from typing import Any

from .singleflight import SingleFlight

__all__ = ("Profile",)


class Profile[S]:
    """A view over several sources that fills itself in on demand.

    Each part is fetched once on first ask and kept, ``None`` included; a loader
    that *raises* keeps nothing, asks arriving while one is in flight share it,
    and a write refreshes the profile so no caller reads a part it invalidated.
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

        A call that raises keeps nothing, so the next ask tries again.  Asks
        arriving while one is in flight wait on it.
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
