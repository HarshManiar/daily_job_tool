"""Token-bucket rate limiting, one bucket per host."""

import asyncio
import time
from collections.abc import Awaitable, Callable

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]

DEFAULT_RATE = 1.0
DEFAULT_BURST = 2
# (host suffix, requests/second). A host matches if it equals the suffix or ends with ".suffix".
HOST_RATES: tuple[tuple[str, float], ...] = (
    ("boards-api.greenhouse.io", 2.0),
    ("api.lever.co", 2.0),
    ("api.eu.lever.co", 2.0),
    ("api.ashbyhq.com", 2.0),
    ("myworkdayjobs.com", 0.5),
)


class TokenBucket:
    def __init__(
        self,
        rate: float,
        burst: int,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate = rate
        self.burst = burst
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(burst)
        self._last = clock()
        self._lock = asyncio.Lock()

    def set_rate(self, rate: float, burst: int | None = None) -> None:
        self._refill()
        self.rate = rate
        if burst is not None:
            self.burst = burst
        self._tokens = min(self._tokens, float(self.burst))

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(float(self.burst), self._tokens + (now - self._last) * self.rate)
        self._last = now

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await self._sleep((1.0 - self._tokens) / self.rate)


class HostLimiter:
    def __init__(
        self,
        default_rate: float = DEFAULT_RATE,
        default_burst: int = DEFAULT_BURST,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        overrides: tuple[tuple[str, float], ...] = HOST_RATES,
    ) -> None:
        self._default_rate = default_rate
        self._default_burst = default_burst
        self._clock = clock
        self._sleep = sleep
        self._overrides = overrides
        self._buckets: dict[str, TokenBucket] = {}
        self._delays: dict[str, float] = {}

    def base_rate(self, host: str) -> float:
        host = host.lower()
        for suffix, rate in self._overrides:
            if host == suffix or host.endswith("." + suffix):
                return rate
        return self._default_rate

    def rate_for(self, host: str) -> float:
        rate = self.base_rate(host)
        delay = self._delays.get(host.lower())
        if delay and delay > 0:
            rate = min(rate, 1.0 / delay)
        return rate

    def _burst_for(self, host: str) -> int:
        return 1 if self._delays.get(host.lower()) else self._default_burst

    def set_crawl_delay(self, host: str, seconds: float) -> None:
        host = host.lower()
        if seconds <= 0:
            return
        self._delays[host] = seconds
        bucket = self._buckets.get(host)
        if bucket is not None:
            bucket.set_rate(self.rate_for(host), self._burst_for(host))

    def _bucket(self, host: str) -> TokenBucket:
        host = host.lower()
        bucket = self._buckets.get(host)
        if bucket is None:
            bucket = TokenBucket(
                self.rate_for(host), self._burst_for(host), self._clock, self._sleep
            )
            self._buckets[host] = bucket
        return bucket

    async def acquire(self, host: str) -> None:
        await self._bucket(host).acquire()
