"""Tiny in-process TTL cache (single-process, single-user app; no locking needed)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class TTLCache:
    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic, maxsize: int = 256):
        self.ttl, self.clock, self.maxsize = ttl, clock, maxsize
        self._d: dict[Hashable, tuple[float, Any]] = {}

    async def get_or_set(self, key: Hashable, factory: Callable[[], Awaitable[Any]]) -> Any:
        now = self.clock()
        hit = self._d.get(key)
        if hit is not None and now - hit[0] < self.ttl:
            return hit[1]
        value = await factory()
        if len(self._d) >= self.maxsize:
            self._d.clear()
        self._d[key] = (now, value)
        return value

    def clear(self) -> None:
        self._d.clear()
