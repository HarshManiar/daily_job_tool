"""robots.txt handling per RFC 9309."""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Protocol
from urllib.parse import urlsplit

from protego import Protego

OK_TTL = 24 * 3600.0
ERROR_TTL = 3600.0
# status 0 means "network error / timeout"
NETWORK_ERROR = 0


class RobotsCache(Protocol):
    async def get(self, host: str) -> tuple[int, str] | None: ...

    async def put(self, host: str, status: int, body: str) -> None: ...


class InMemoryRobotsCache:
    """24h TTL, except 5xx / network failures which are cached for 1h only."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[str, tuple[float, int, str]] = {}

    async def get(self, host: str) -> tuple[int, str] | None:
        entry = self._data.get(host)
        if entry is None:
            return None
        expires, status, body = entry
        if self._clock() >= expires:
            del self._data[host]
            return None
        return status, body

    async def put(self, host: str, status: int, body: str) -> None:
        ttl = ERROR_TTL if (status < 200 or status >= 500) else OK_TTL
        self._data[host] = (self._clock() + ttl, status, body)


# fetcher(robots_url) -> (status, body); status 0 on network error/timeout.
RobotsFetcher = Callable[[str], Awaitable[tuple[int, str]]]


def _product_token(user_agent: str) -> str:
    m = re.match(r"[A-Za-z0-9_-]+", user_agent.strip())
    return m.group(0) if m else user_agent


class RobotsPolicy:
    def __init__(self, cache: RobotsCache, fetcher: RobotsFetcher) -> None:
        self._cache = cache
        self._fetcher = fetcher
        self._parsed: dict[str, tuple[int, str, Protego | None]] = {}
        self._delays: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _rules(self, scheme: str, host: str) -> tuple[int, Protego | None]:
        async with self._locks.setdefault(host, asyncio.Lock()):
            cached = await self._cache.get(host)
            if cached is None:
                status, body = await self._fetcher(f"{scheme}://{host}/robots.txt")
                await self._cache.put(host, status, body)
            else:
                status, body = cached
            memo = self._parsed.get(host)
            if memo and memo[0] == status and memo[1] == body:
                return status, memo[2]
            parsed = Protego.parse(body) if 200 <= status < 300 else None
            self._parsed[host] = (status, body, parsed)
            return status, parsed

    async def can_fetch(self, url: str, user_agent: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc.lower()
        status, rules = await self._rules(parts.scheme or "https", host)
        if 200 <= status < 300 and rules is not None:
            token = _product_token(user_agent)
            delay = rules.crawl_delay(token)
            if delay:
                self._delays[host] = float(delay)
            return bool(rules.can_fetch(url, token))
        if 400 <= status < 500:
            return True
        return False  # 5xx, timeout, network error, unresolved redirect: disallow

    def crawl_delay(self, host: str) -> float | None:
        return self._delays.get(host.lower())
