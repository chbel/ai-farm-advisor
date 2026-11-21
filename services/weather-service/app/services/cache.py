"""Small in-memory TTL cache for provider responses.

Weather data changes slowly (Open-Meteo models update roughly hourly), so
caching avoids repeated provider calls for the same farm and keeps us well
within rate limits. The cache is per process and is lost on restart, which is
acceptable: it only holds data that can be fetched again.
"""

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import TypeVar

T = TypeVar("T")


class TTLCache:
    def __init__(self, max_entries: int) -> None:
        self._max_entries = max_entries
        self._items: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()

    async def get_or_fetch(
        self, key: Hashable, ttl_seconds: float, fetch: Callable[[], Awaitable[T]]
    ) -> T:
        now = time.monotonic()
        hit = self._items.get(key)
        if hit is not None and hit[0] > now:
            self._items.move_to_end(key)
            return hit[1]  # type: ignore[return-value]

        value = await fetch()  # errors are not cached
        self._items[key] = (now + ttl_seconds, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)  # evict least recently used
        return value

    def clear(self) -> None:
        self._items.clear()


def location_key(latitude: float, longitude: float) -> tuple[float, float]:
    """Round to 0.01° (about 1 km), finer than the provider's model grid."""
    return round(latitude, 2), round(longitude, 2)
