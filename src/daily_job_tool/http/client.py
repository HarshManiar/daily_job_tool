"""CrawlClient: the only way adapters touch the network."""

import asyncio
import contextvars
import http.cookiejar
import random
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from daily_job_tool.http.blocklist import BlockedCheck, is_blocked_host
from daily_job_tool.http.errors import (
    Blocked,
    CrawlDelayTooLong,
    DomainBlocked,
    FetchError,
    HostCircuitOpen,
    NotFound,
    ResponseTooLarge,
    RobotsDisallowed,
)
from daily_job_tool.http.ratelimit import HostLimiter, Sleep
from daily_job_tool.http.robots import (
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
AKAMAI_MARKERS = ("Reference #", "edgesuite.net")  # both must appear
MAX_RETRY_AFTER = 600.0
CIRCUIT_WINDOW = 20
CIRCUIT_MIN_SAMPLES = 10
CIRCUIT_FAIL_RATIO = 0.5
CIRCUIT_PAUSE = 30 * 60.0
ROBOTS_MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 5
MAX_CRAWL_DELAY = 60.0
REQUEST_DEADLINE = 90.0  # wall-clock cap on one send + body read (a stalled socket after sleep)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


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


@dataclass
class Accounting:
    """Per-run counters from `CrawlClient.accounting()`. Includes robots.txt fetches."""

    requests: int = 0
    errors: int = 0
    bytes: int = 0
    robots_requests: int = 0  # subset of `requests`
    hosts: dict[str, HostStats] = field(default_factory=dict)

    def host(self, host: str) -> HostStats:
        return self.hosts.setdefault(host, HostStats())


_ACCT: contextvars.ContextVar[Accounting | None] = contextvars.ContextVar("acct", default=None)


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
    """The only way adapters touch the network.

    Every hop of every request (including redirects) goes through: aggregator blocklist,
    circuit breaker, robots.txt, global concurrency semaphore, per-host token bucket.
    Redirects are followed manually (max 5 hops) so no hop can bypass those checks.

    Attempts: 1 + len(retry_delays) = 4 total per hop with the default (2, 8, 30) delays.

    Stats: `stats` / `request_count` are client-global and EXCLUDE robots.txt fetches.
    `accounting()` gives per-run counters (contextvar-scoped, so concurrent sources get
    separate counts) and INCLUDES robots.txt fetches.
    """

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
        blocked: BlockedCheck | None = None,
        request_deadline: float | None = REQUEST_DEADLINE,
    ) -> None:
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.retry_delays = tuple(retry_delays)
        self.recorder = recorder
        self.request_deadline = request_deadline
        # blocked(hostname) -> True if the host must never be fetched
        self._blocked: BlockedCheck = blocked or is_blocked_host
        self._clock = clock
        self._sleep = sleep
        self._limiter = limiter or HostLimiter(clock=clock, sleep=sleep)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._http = httpx.AsyncClient(
            transport=transport,
            http2=False,
            follow_redirects=False,
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

    @contextmanager
    def accounting(self) -> Iterator[Accounting]:
        """Count requests/errors/bytes for work done inside this context (and its tasks)."""
        acct = Accounting()
        token = _ACCT.set(acct)
        try:
            yield acct
        finally:
            _ACCT.reset(token)

    def _count(
        self,
        host: str,
        *,
        requests: int = 0,
        errors: int = 0,
        nbytes: int = 0,
        robots: bool = False,
    ) -> None:
        if not robots:
            st = self.stats.setdefault(host, HostStats())
            st.requests += requests
            st.errors += errors
            st.bytes += nbytes
            self.request_count += requests
        acct = _ACCT.get()
        if acct is not None:
            ast = acct.host(host)
            for a in (acct, ast):
                a.requests += requests
                a.errors += errors
                a.bytes += nbytes
            if robots:
                acct.robots_requests += requests

    # -- circuit ---------------------------------------------------------
    def _circuit_is_open(self, host: str) -> bool:
        until = self._open_until.get(host)
        if until is None:
            return False
        if self._clock() < until:
            return True
        del self._open_until[host]
        self._outcomes.pop(host, None)
        return False

    def _check_circuit(self, host: str) -> None:
        if self._circuit_is_open(host):
            raise HostCircuitOpen(host)

    def _note_outcome(self, host: str, failed: bool) -> None:
        window = self._outcomes.setdefault(host, deque(maxlen=CIRCUIT_WINDOW))
        window.append(failed)
        if len(window) >= CIRCUIT_MIN_SAMPLES and sum(window) / len(window) > CIRCUIT_FAIL_RATIO:
            self._open_until[host] = self._clock() + CIRCUIT_PAUSE

    # -- blocklist / redirects ------------------------------------------
    def _check_blocked(self, url: str) -> None:
        hostname = (urlsplit(url).hostname or "").lower()
        if not hostname or self._blocked(hostname):
            raise DomainBlocked(hostname or url)

    @staticmethod
    def _next_url(current: str, location: str) -> str | None:
        """Absolute target of a redirect, or None if it is not acceptable."""
        target = urldefrag(urljoin(current, location.strip()))[0]
        cur, nxt = urlsplit(current), urlsplit(target)
        if nxt.scheme not in ("http", "https") or not nxt.netloc:
            return None
        if cur.scheme == "https" and nxt.scheme == "http":
            return None  # never downgrade
        return target

    # -- robots ----------------------------------------------------------
    async def _fetch_robots(self, robots_url: str) -> tuple[int, str]:
        """RFC 9309: follow up to 5 redirects; every hop is blocklist-checked and rate limited."""
        cur = robots_url
        for _ in range(MAX_REDIRECTS + 1):
            host = urlsplit(cur).netloc.lower()
            try:
                self._check_blocked(cur)
                resp = await self._send_once(
                    "GET", cur, None, host, cap=ROBOTS_MAX_BYTES, truncate=True, robots=True
                )
            except (DomainBlocked, httpx.HTTPError):
                return NETWORK_ERROR, ""
            location = resp.headers.get("location")
            if resp.status_code in REDIRECT_STATUSES and location:
                nxt = self._next_url(cur, location)
                if nxt is None:
                    return NETWORK_ERROR, ""
                cur = nxt
                continue
            return resp.status_code, resp.content.decode("utf-8", errors="replace")
        return NETWORK_ERROR, ""

    async def _check_robots(self, url: str, host: str) -> None:
        if not await self._robots.can_fetch(url, self.user_agent):
            raise RobotsDisallowed(url)
        delay = self._robots.crawl_delay(host)
        if delay:
            if delay > MAX_CRAWL_DELAY:
                raise CrawlDelayTooLong(host, delay)
            self._limiter.set_crawl_delay(host, delay)

    # -- core ------------------------------------------------------------
    async def _send_once(
        self,
        method: str,
        url: str,
        json: Any,
        host: str,
        *,
        cap: int | None = None,
        truncate: bool = False,
        robots: bool = False,
    ) -> httpx.Response:
        """One HTTP exchange. Semaphore first, then the host's token, then send: the token is
        taken at the moment of sending so waiting for a slot can never cause a burst."""
        cap = self.max_bytes if cap is None else cap
        req = self._http.build_request(method, url, json=json)
        req.headers["User-Agent"] = self.user_agent
        async with self._sem:
            await self._limiter.acquire(host)
            self._count(host, requests=1, robots=robots)
            try:
                async with asyncio.timeout(self.request_deadline):
                    resp, chunks = await self._exchange(req, url, cap, truncate)
            except TimeoutError as exc:
                # httpx timeouts only bound individual socket ops; this bounds the whole attempt.
                raise httpx.ReadTimeout(
                    f"request exceeded {self.request_deadline}s wall-clock deadline", request=req
                ) from exc
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

    async def _exchange(
        self, req: httpx.Request, url: str, cap: int, truncate: bool
    ) -> tuple[httpx.Response, list[bytes]]:
        resp = await self._http.send(req, stream=True, follow_redirects=False)
        try:
            declared = resp.headers.get("content-length")
            if not truncate and declared and declared.isdigit() and int(declared) > cap:
                raise ResponseTooLarge(url, cap)
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > cap:
                    if not truncate:
                        raise ResponseTooLarge(url, cap)
                    chunks.append(chunk[: len(chunk) - (total - cap)])
                    break
                chunks.append(chunk)
        finally:
            await resp.aclose()
        return resp, chunks

    def _jittered(self, attempt: int) -> float:
        return self.retry_delays[attempt] * random.uniform(0.8, 1.2)

    @staticmethod
    def _is_challenge(resp: httpx.Response) -> bool:
        if resp.headers.get("cf-mitigated", "").lower() == "challenge":
            return True
        if resp.status_code == 403 and "akamaighost" in resp.headers.get("server", "").lower():
            return True
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code < 400 and "html" not in ctype:
            return False
        head = resp.content[:65536].decode("utf-8", errors="ignore")
        if all(m in head for m in AKAMAI_MARKERS):
            return True
        return any(marker in head for marker in BLOCK_MARKERS)

    async def request(
        self, method: str, url: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> httpx.Response:
        """Fetch `url`, following up to 5 redirects manually (each hop fully policed).

        Only `params` and `json` are accepted: callers cannot set headers or cookies.
        Fixtures are recorded under the original URL.
        """
        method = method.upper()
        first = str(self._http.build_request(method, url, params=params).url)
        cur, cur_method, cur_json = first, method, json
        for hop in range(MAX_REDIRECTS + 1):
            resp = await self._fetch_hop(cur_method, cur, cur_json, (method, first, json))
            location = resp.headers.get("location")
            if resp.status_code not in REDIRECT_STATUSES or not location:
                return resp
            if hop == MAX_REDIRECTS:
                raise FetchError(first, resp.status_code, f"more than {MAX_REDIRECTS} redirects")
            nxt = self._next_url(cur, location)
            if nxt is None:
                raise FetchError(first, resp.status_code, f"refusing redirect to {location!r}")
            if resp.status_code == 303 or (resp.status_code in (301, 302) and cur_method == "POST"):
                cur_method, cur_json = "GET", None
            cur = nxt
        raise AssertionError("unreachable")  # pragma: no cover

    async def _fetch_hop(
        self, method: str, url: str, json: Any, rec: tuple[str, str, Any]
    ) -> httpx.Response:
        host = urlsplit(url).netloc.lower()
        self._check_blocked(url)
        self._check_circuit(host)
        await self._check_robots(url, host)

        last = len(self.retry_delays)
        for attempt in range(last + 1):
            try:
                resp = await self._send_once(method, url, json, host)
            except ResponseTooLarge:
                self._count(host, errors=1)
                self._note_outcome(host, False)
                raise
            except (httpx.TransportError, httpx.DecodingError) as exc:  # incl. timeouts
                self._count(host, errors=1)
                self._note_outcome(host, True)
                if attempt == last:
                    raise FetchError(url, None, f"{type(exc).__name__}: {exc}") from exc
                if self._circuit_is_open(host):
                    raise FetchError(url, None, "circuit opened; giving up") from exc
                await self._sleep(self._jittered(attempt))
                continue

            status = resp.status_code
            self._count(host, nbytes=len(resp.content))

            if self._is_challenge(resp) or status in (401, 403):
                self._count(host, errors=1)
                self._note_outcome(host, False)
                raise Blocked(url, status)
            if status in (404, 410):
                self._count(host, errors=1)
                self._note_outcome(host, False)
                self._record(rec, resp)
                raise NotFound(url, status)
            if status < 400:
                self._note_outcome(host, False)
                if not (status in REDIRECT_STATUSES and resp.headers.get("location")):
                    self._record(rec, resp)
                return resp
            self._count(host, errors=1)
            if status == 429 or status >= 500:
                self._note_outcome(host, True)
                retry_after = _parse_retry_after(resp.headers.get("retry-after"))
                if retry_after is not None and retry_after > MAX_RETRY_AFTER:
                    raise FetchError(url, status, f"Retry-After {retry_after:.0f}s too long")
                if attempt == last:
                    raise FetchError(url, status, "retries exhausted")
                if self._circuit_is_open(host):
                    raise FetchError(url, status, "circuit opened; giving up")
                if retry_after is not None and status in (429, 503):
                    self._limiter.pause(host, retry_after)  # whole host backs off, not just us
                delay = retry_after if retry_after is not None else self._jittered(attempt)
                await self._sleep(delay)
                continue
            self._note_outcome(host, False)
            raise FetchError(url, status, f"HTTP {status}")
        raise AssertionError("unreachable")  # pragma: no cover

    def _record(self, rec: tuple[str, str, Any], resp: httpx.Response) -> None:
        if self.recorder is None:
            return
        method, url, request_json = rec  # the URL as requested, not the post-redirect one
        self.recorder.record(
            method, url, request_json, resp.status_code, resp.headers, resp.content
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
