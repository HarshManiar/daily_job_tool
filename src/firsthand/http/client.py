"""CrawlClient: the only way adapters touch the network."""

import asyncio
import http.cookiejar
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from firsthand.http.errors import (
    Blocked,
    FetchError,
    HostCircuitOpen,
    NotFound,
    ResponseTooLarge,
    RobotsDisallowed,
)
from firsthand.http.ratelimit import HostLimiter, Sleep
from firsthand.http.robots import (
    NETWORK_ERROR,
    InMemoryRobotsCache,
    RobotsCache,
    RobotsPolicy,
)

BLOCK_MARKERS = (
    "cf-chl",
    "Attention Required! | Cloudflare",
    "_Incapsula_Resource",
    "px-captcha",
)
MAX_RETRY_AFTER = 600.0
CIRCUIT_WINDOW = 20
CIRCUIT_MIN_SAMPLES = 10
CIRCUIT_FAIL_RATIO = 0.5
CIRCUIT_PAUSE = 30 * 60.0
ROBOTS_MAX_BYTES = 512 * 1024


class Recorder(Protocol):
    def record(
        self,
        method: str,
        url: str,
        request_json: Any,
        status: int,
        headers: httpx.Headers,
        body: bytes,
    ) -> None: ...


@dataclass
class HostStats:
    requests: int = 0
    errors: int = 0
    bytes: int = 0


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class CrawlClient:
    def __init__(
        self,
        user_agent: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        robots_cache: RobotsCache | None = None,
        limiter: HostLimiter | None = None,
        max_concurrency: int = 16,
        max_bytes: int = 25 * 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        retry_delays: tuple[float, ...] = (2, 8, 30),
        recorder: Recorder | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.retry_delays = tuple(retry_delays)
        self.recorder = recorder
        self._clock = clock
        self._sleep = sleep
        self._limiter = limiter or HostLimiter(clock=clock, sleep=sleep)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._http = httpx.AsyncClient(
            transport=transport,
            http2=False,
            follow_redirects=True,
            timeout=httpx.Timeout(30, connect=10),
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            },
        )
        # Never persist cookies between requests: block every domain.
        self._http.cookies.jar.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
        self._robots = RobotsPolicy(robots_cache or InMemoryRobotsCache(clock), self._fetch_robots)
        self._outcomes: dict[str, deque[bool]] = {}  # True = failure
        self._open_until: dict[str, float] = {}
        self.stats: dict[str, HostStats] = {}
        self.request_count = 0

    # -- lifecycle -------------------------------------------------------
    async def __aenter__(self) -> "CrawlClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- accounting ------------------------------------------------------
    def reset_stats(self) -> None:
        self.stats = {}
        self.request_count = 0

    def _stat(self, host: str) -> HostStats:
        return self.stats.setdefault(host, HostStats())

    # -- circuit ---------------------------------------------------------
    def _check_circuit(self, host: str) -> None:
        until = self._open_until.get(host)
        if until is None:
            return
        if self._clock() < until:
            raise HostCircuitOpen(host)
        del self._open_until[host]
        self._outcomes.pop(host, None)

    def _note_outcome(self, host: str, failed: bool) -> None:
        window = self._outcomes.setdefault(host, deque(maxlen=CIRCUIT_WINDOW))
        window.append(failed)
        if len(window) >= CIRCUIT_MIN_SAMPLES and sum(window) / len(window) > CIRCUIT_FAIL_RATIO:
            self._open_until[host] = self._clock() + CIRCUIT_PAUSE

    # -- robots ----------------------------------------------------------
    async def _fetch_robots(self, robots_url: str) -> tuple[int, str]:
        host = urlsplit(robots_url).netloc.lower()
        await self._limiter.acquire(host)
        try:
            async with self._sem:
                resp = await self._http.get(robots_url)
        except httpx.HTTPError:
            return NETWORK_ERROR, ""
        body = resp.content[:ROBOTS_MAX_BYTES].decode("utf-8", errors="replace")
        return resp.status_code, body

    async def _check_robots(self, url: str, host: str) -> None:
        if not await self._robots.can_fetch(url, self.user_agent):
            raise RobotsDisallowed(url)
        delay = self._robots.crawl_delay(host)
        if delay:
            self._limiter.set_crawl_delay(host, delay)

    # -- core ------------------------------------------------------------
    async def _send_once(self, method: str, url: str, **kw: Any) -> httpx.Response:
        req = self._http.build_request(method, url, **kw)
        async with self._sem:
            resp = await self._http.send(req, stream=True)
            try:
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self.max_bytes:
                    raise ResponseTooLarge(url, self.max_bytes)
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ResponseTooLarge(url, self.max_bytes)
                    chunks.append(chunk)
            finally:
                await resp.aclose()
        # body is already decoded; drop encoding/length headers so the copy is self-consistent
        headers = httpx.Headers(resp.headers)
        for h in ("content-encoding", "content-length", "transfer-encoding"):
            headers.pop(h, None)
        return httpx.Response(
            resp.status_code,
            headers=headers,
            content=b"".join(chunks),
            request=resp.request,
        )

    def _jittered(self, attempt: int) -> float:
        return self.retry_delays[attempt] * random.uniform(0.8, 1.2)

    @staticmethod
    def _is_challenge(resp: httpx.Response) -> bool:
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code < 400 and "html" not in ctype:
            return False
        head = resp.content[:65536].decode("utf-8", errors="ignore")
        return any(marker in head for marker in BLOCK_MARKERS)

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        method = method.upper()
        host = urlsplit(url).netloc.lower()
        await self._check_robots(url, host)
        self._check_circuit(host)

        last = len(self.retry_delays)
        for attempt in range(last + 1):
            await self._limiter.acquire(host)
            stat = self._stat(host)
            stat.requests += 1
            self.request_count += 1
            try:
                resp = await self._send_once(method, url, **kw)
            except ResponseTooLarge:
                stat.errors += 1
                self._note_outcome(host, False)
                raise
            except (httpx.TransportError, httpx.DecodingError) as exc:  # incl. timeouts
                stat.errors += 1
                self._note_outcome(host, True)
                if attempt == last:
                    raise FetchError(url, None, f"{type(exc).__name__}: {exc}") from exc
                await self._sleep(self._jittered(attempt))
                continue

            status = resp.status_code
            stat.bytes += len(resp.content)

            if self._is_challenge(resp) or status in (401, 403):
                stat.errors += 1
                self._note_outcome(host, False)
                raise Blocked(url, status)
            if status == 404:
                stat.errors += 1
                self._note_outcome(host, False)
                self._record(method, url, kw, resp)
                raise NotFound(url)
            if status < 400:
                self._note_outcome(host, False)
                self._record(method, url, kw, resp)
                return resp
            stat.errors += 1
            if status == 429 or status >= 500:
                self._note_outcome(host, True)
                retry_after = _parse_retry_after(resp.headers.get("retry-after"))
                if retry_after is not None and retry_after > MAX_RETRY_AFTER:
                    raise FetchError(url, status, f"Retry-After {retry_after:.0f}s too long")
                if attempt == last:
                    raise FetchError(url, status, "retries exhausted")
                delay = retry_after if retry_after is not None else self._jittered(attempt)
                await self._sleep(delay)
                continue
            self._note_outcome(host, False)
            raise FetchError(url, status, f"HTTP {status}")
        raise AssertionError("unreachable")  # pragma: no cover

    def _record(self, method: str, url: str, kw: dict[str, Any], resp: httpx.Response) -> None:
        if self.recorder is None:
            return
        # record against the URL as requested (with params), not the post-redirect one
        req_url = str(self._http.build_request(method, url, params=kw.get("params")).url)
        self.recorder.record(
            method, req_url, kw.get("json"), resp.status_code, resp.headers, resp.content
        )

    # -- conveniences ----------------------------------------------------
    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self.request("GET", url, params=params)
        return self._json(url, resp)

    async def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        return (await self.request("GET", url, params=params)).text

    async def post_json(self, url: str, json: Any) -> Any:
        return self._json(url, await self.request("POST", url, json=json))

    @staticmethod
    def _json(url: str, resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError as exc:
            raise FetchError(url, resp.status_code, "invalid JSON") from exc
