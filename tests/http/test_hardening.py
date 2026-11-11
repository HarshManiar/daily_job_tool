"""Redirects, blocklist, rate-limit ordering, accounting, challenge detection, etc."""

import asyncio
import json

import httpx
import pytest

from firsthand.http.blocklist import BUILTIN_BLOCKED, is_blocked_host, load_blocked_domains
from firsthand.http.client import CrawlClient
from firsthand.http.errors import (
    Blocked,
    CrawlDelayTooLong,
    DomainBlocked,
    FetchError,
    NotFound,
    RobotsDisallowed,
)
from firsthand.http.fixtures import FixtureRecorder, FixtureTransport
from firsthand.http.ratelimit import HostLimiter

UA = "FirsthandBot/0.1 (+https://example.invalid/bot)"


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s
        await asyncio.sleep(0)


class Net:
    """Routes {(host, path): Response | callable(request)}; missing robots.txt is 404."""

    def __init__(self, routes=None) -> None:
        self.routes = routes or {}
        self.log: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.log.append(str(request.url))
        r = self.routes.get((request.url.host, request.url.path))
        if r is None:
            return httpx.Response(404 if request.url.path == "/robots.txt" else 500)
        if callable(r):
            r = r(request)
            if asyncio.iscoroutine(r):
                r = await r
        return r


def redirect(to: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": to})


def make(net: Net, t: FakeTime | None = None, **kw) -> CrawlClient:
    t = t or FakeTime()
    limiter = kw.pop("limiter", None) or HostLimiter(
        overrides=(), default_rate=1000, default_burst=1000, clock=t.clock, sleep=t.sleep
    )
    kw.setdefault("retry_delays", (0, 0, 0))
    c = CrawlClient(
        UA, transport=httpx.MockTransport(net), limiter=limiter, clock=t.clock, sleep=t.sleep, **kw
    )
    c.t = t  # type: ignore[attr-defined]
    return c


# -- blocklist ----------------------------------------------------------
def test_blocklist_suffix_matching():
    assert is_blocked_host("linkedin.com")
    assert is_blocked_host("www.linkedin.com")
    assert is_blocked_host("WWW.LinkedIn.com")
    assert not is_blocked_host("notlinkedin.com")
    assert not is_blocked_host("linkedin.com.evil.example")
    assert {"linkedin.com", "indeed.com", "talent.com", "angel.co"} <= BUILTIN_BLOCKED


def test_load_blocklist_file_and_fallback(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("domain,reason\n# c\nFoo.com,x\n\nbar.org\n")
    assert load_blocked_domains(f) == {"foo.com", "bar.org"}
    assert load_blocked_domains(tmp_path / "missing.csv") == BUILTIN_BLOCKED


async def test_blocked_before_robots_and_network():
    net = Net()
    async with make(net) as c:
        with pytest.raises(DomainBlocked):
            await c.get_text("https://www.linkedin.com/jobs/1")
    assert net.log == []  # not even robots.txt


async def test_custom_blocked_hook():
    net = Net({("ok.example", "/x"): httpx.Response(200, text="x")})
    async with make(net, blocked=lambda h: h == "ok.example") as c:
        with pytest.raises(DomainBlocked):
            await c.get_text("https://ok.example/x")


# -- redirects ----------------------------------------------------------
async def test_redirect_to_robots_disallowed_host():
    net = Net(
        {
            ("a.example", "/start"): redirect("https://b.example/secret"),
            ("b.example", "/robots.txt"): httpx.Response(200, text="User-agent: *\nDisallow: /\n"),
            ("b.example", "/secret"): httpx.Response(200, text="secret"),
        }
    )
    async with make(net) as c:
        with pytest.raises(RobotsDisallowed):
            await c.get_text("https://a.example/start")
    assert "https://b.example/secret" not in net.log


async def test_redirect_to_blocked_domain():
    net = Net({("a.example", "/start"): redirect("https://www.linkedin.com/jobs/1")})
    async with make(net) as c:
        with pytest.raises(DomainBlocked):
            await c.get_text("https://a.example/start")
    assert not any("linkedin" in u for u in net.log)


async def test_too_many_redirects():
    net = Net({("a.example", f"/{i}"): redirect(f"https://a.example/{i + 1}") for i in range(20)})
    async with make(net) as c:
        with pytest.raises(FetchError, match="redirects"):
            await c.get_text("https://a.example/0")
    assert len([u for u in net.log if "robots" not in u]) == 6  # original + 5 hops


async def test_five_redirects_ok():
    routes = {("a.example", f"/{i}"): redirect(f"https://a.example/{i + 1}") for i in range(5)}
    routes[("a.example", "/5")] = httpx.Response(200, text="done")
    async with make(Net(routes)) as c:
        assert await c.get_text("https://a.example/0") == "done"


async def test_https_to_http_downgrade_refused():
    net = Net(
        {
            ("a.example", "/s"): redirect("http://a.example/plain"),
            ("a.example", "/plain"): httpx.Response(200, text="x"),
        }
    )
    async with make(net) as c:
        with pytest.raises(FetchError, match="refusing"):
            await c.get_text("https://a.example/s")
    assert "http://a.example/plain" not in net.log


async def test_relative_redirect_charges_same_host_and_records_original(tmp_path):
    net = Net(
        {
            ("a.example", "/s"): redirect("/final?x=1", 301),
            ("a.example", "/final"): httpx.Response(200, json={"ok": 1}),
        }
    )
    rec = FixtureRecorder(tmp_path / "ad" / "tok")
    async with make(net, recorder=rec) as c:
        assert await c.get_json("https://a.example/s") == {"ok": 1}
        assert c.stats["a.example"].requests == 2
        assert c.request_count == 2
    m = json.loads(rec.manifest_path.read_text())
    assert [e["url"] for e in m["exchanges"]] == ["https://a.example/s"]


async def test_redirect_hop_is_rate_limited_per_host():
    t = FakeTime()
    lim = HostLimiter(overrides=(), default_rate=1, default_burst=1, clock=t.clock, sleep=t.sleep)
    net = Net(
        {
            ("a.example", "/s"): redirect("https://b.example/x"),
            ("b.example", "/x"): httpx.Response(200, text="ok"),
        }
    )
    async with make(net, t, limiter=lim) as c:
        await c.get_text("https://a.example/s")
        c.stats.clear()
        await c.get_text("https://b.example/x")  # b's bucket already used by the redirect hop
    assert t.sleeps  # second b request had to wait


async def test_post_redirect_303_becomes_get():
    seen = []

    def final(req):
        seen.append((req.method, req.content))
        return httpx.Response(200, text="ok")

    net = Net({("a.example", "/p"): redirect("/g", 303), ("a.example", "/g"): final})
    async with make(net) as c:
        r = await c.request("POST", "https://a.example/p", json={"a": 1})
    assert r.text == "ok" and seen == [("GET", b"")]


# -- kw restriction / headers ------------------------------------------
async def test_cannot_override_ua_or_cookies():
    net = Net({("a.example", "/x"): httpx.Response(200, text="ok")})
    async with make(net) as c:
        with pytest.raises(TypeError):
            await c.request("GET", "https://a.example/x", headers={"User-Agent": "evil"})  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            await c.request("GET", "https://a.example/x", cookies={"a": "b"})  # type: ignore[call-arg]


# -- rate limit ordering ------------------------------------------------
async def test_no_burst_after_waiting_on_semaphore():
    t = FakeTime()
    sends: list[float] = []

    def a(req):
        sends.append(t.now)
        return httpx.Response(200, text="ok")

    async def slow(req):
        t.now += 10  # a slow request to another host hogs the only slot
        await asyncio.sleep(0)
        return httpx.Response(200, text="ok")

    net = Net({("a.example", "/x"): a, ("slow.example", "/x"): slow})
    lim = HostLimiter(overrides=(), default_rate=5, default_burst=1, clock=t.clock, sleep=t.sleep)
    async with make(net, t, limiter=lim, max_concurrency=1) as c:
        tasks = [asyncio.create_task(c.get_text("https://slow.example/x"))]
        await asyncio.sleep(0)
        tasks += [asyncio.create_task(c.get_text("https://a.example/x")) for _ in range(8)]
        await asyncio.gather(*tasks)
    assert len(sends) == 8
    for prev, nxt in zip(sends, sends[1:], strict=False):
        assert nxt - prev >= 0.2 - 1e-9


async def test_pause_blocks_concurrent_requests():
    t = FakeTime()
    lim = HostLimiter(
        overrides=(), default_rate=1000, default_burst=1000, clock=t.clock, sleep=t.sleep
    )
    lim.pause("h.example", 5)
    await lim.acquire("h.example")
    assert t.now >= 1005.0
    t0 = t.now
    await lim.acquire("other.example")
    assert t.now == t0


async def test_429_retry_after_pauses_host():
    calls = []
    net = Net(
        {
            ("a.example", "/x"): lambda r: (
                httpx.Response(429, headers={"retry-after": "7"})
                if not calls.append(1) and len(calls) == 1
                else httpx.Response(200, text="ok")
            )
        }
    )
    t = FakeTime()
    lim = HostLimiter(
        overrides=(), default_rate=1000, default_burst=1000, clock=t.clock, sleep=t.sleep
    )
    paused: list[tuple[str, float]] = []
    orig = lim.pause
    lim.pause = lambda h, s: (paused.append((h, s)), orig(h, s))[1]  # type: ignore[method-assign]
    async with make(net, t, limiter=lim) as c:
        assert await c.get_text("https://a.example/x") == "ok"
    assert paused == [("a.example", 7.0)]


# -- robots -------------------------------------------------------------
async def test_robots_body_truncated_streaming():
    big = b"User-agent: *\nAllow: /\n" + b"#" * (2 * 1024 * 1024)
    net = Net(
        {
            ("a.example", "/robots.txt"): httpx.Response(200, content=big),
            ("a.example", "/x"): httpx.Response(200, text="ok"),
        }
    )
    async with make(net, max_bytes=1000) as c:  # max_bytes must not apply to robots
        assert await c.get_text("https://a.example/x") == "ok"


@pytest.mark.parametrize("status", [429, 503])
async def test_robots_429_503_disallow(status):
    net = Net(
        {
            ("a.example", "/robots.txt"): httpx.Response(status),
            ("a.example", "/x"): httpx.Response(200, text="ok"),
        }
    )
    async with make(net) as c:
        with pytest.raises(RobotsDisallowed):
            await c.get_text("https://a.example/x")
    assert "https://a.example/x" not in net.log


async def test_robots_cache_ttl_429_is_one_hour():
    from firsthand.http.robots import InMemoryRobotsCache

    now = [0.0]
    cache = InMemoryRobotsCache(lambda: now[0])
    await cache.put("a", 429, "")
    now[0] = 3601
    assert await cache.get("a") is None


async def test_robots_redirect_followed_but_blocklist_applies():
    net = Net(
        {
            ("a.example", "/robots.txt"): redirect("https://www.linkedin.com/robots.txt"),
            ("a.example", "/x"): httpx.Response(200, text="ok"),
        }
    )
    async with make(net) as c:
        with pytest.raises(RobotsDisallowed):  # blocked hop -> unreachable -> disallow
            await c.get_text("https://a.example/x")
    assert not any("linkedin" in u for u in net.log)


async def test_robots_redirect_followed_same_host():
    net = Net(
        {
            ("a.example", "/robots.txt"): redirect("/real-robots.txt"),
            ("a.example", "/real-robots.txt"): httpx.Response(
                200, text="User-agent: *\nDisallow: /x\n"
            ),
            ("a.example", "/x"): httpx.Response(200, text="ok"),
        }
    )
    async with make(net) as c:
        with pytest.raises(RobotsDisallowed):
            await c.get_text("https://a.example/x")


async def test_robots_redirect_loop_bounded():
    net = Net({("a.example", "/robots.txt"): redirect("/robots.txt")})
    async with make(net) as c:
        with pytest.raises(RobotsDisallowed):
            await c.get_text("https://a.example/x")
    assert len(net.log) == 6


async def test_crawl_delay_too_long_skips_host():
    net = Net(
        {
            ("a.example", "/robots.txt"): httpx.Response(
                200, text="User-agent: *\nCrawl-delay: 120\n"
            ),
            ("a.example", "/x"): httpx.Response(200, text="ok"),
        }
    )
    async with make(net) as c:
        with pytest.raises(CrawlDelayTooLong):
            await c.get_text("https://a.example/x")
    assert "https://a.example/x" not in net.log


async def test_circuit_checked_before_robots():
    net = Net({("a.example", "/x"): httpx.Response(503)})
    async with make(net) as c:
        for _ in range(3):
            with pytest.raises(FetchError):
                await c.get_text("https://a.example/x")
        net.log.clear()
        from firsthand.http.errors import HostCircuitOpen

        with pytest.raises(HostCircuitOpen):
            await c.get_text("https://a.example/x")
    assert net.log == []


# -- accounting ---------------------------------------------------------
async def test_accounting_separate_per_task_includes_robots():
    net = Net(
        {
            ("a.example", "/x"): httpx.Response(200, text="aaaa"),
            ("b.example", "/y"): httpx.Response(200, text="bb"),
        }
    )
    async with make(net) as c:

        async def run(url: str, n: int):
            with c.accounting() as acct:
                for _ in range(n):
                    await c.get_text(url)
                    await asyncio.sleep(0)
            return acct

        a, b = await asyncio.gather(run("https://a.example/x", 3), run("https://b.example/y", 1))
        assert c.request_count == 4  # global excludes robots
    assert (a.requests, a.robots_requests, a.bytes) == (4, 1, 12)  # 3 + robots
    assert (b.requests, b.robots_requests, b.bytes) == (2, 1, 2)
    assert set(a.hosts) == {"a.example"} and a.hosts["a.example"].requests == 4


# -- challenge detection / status ---------------------------------------
@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(200, headers={"cf-mitigated": "challenge"}, text="x"),
        httpx.Response(403, headers={"server": "AkamaiGHost"}, text="x"),
        httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="Access Denied Reference #18.1 https://errors.edgesuite.net/18.1",
        ),
    ],
)
async def test_challenge_variants_blocked(resp):
    net = Net({("a.example", "/x"): resp})
    async with make(net) as c:
        with pytest.raises(Blocked):
            await c.get_text("https://a.example/x")
    assert net.log.count("https://a.example/x") == 1


async def test_reference_marker_alone_is_not_challenge():
    net = Net(
        {
            ("a.example", "/x"): httpx.Response(
                200, headers={"content-type": "text/html"}, text="Reference # 5"
            )
        }
    )
    async with make(net) as c:
        assert await c.get_text("https://a.example/x") == "Reference # 5"


async def test_410_gone_is_not_found():
    net = Net({("a.example", "/x"): httpx.Response(410)})
    async with make(net) as c:
        with pytest.raises(NotFound) as ei:
            await c.get_text("https://a.example/x")
    assert ei.value.status == 410


# -- fixtures -----------------------------------------------------------
async def test_fixture_headers_round_trip_and_old_manifest(tmp_path):
    d = tmp_path / "ad" / "tok"
    net = Net(
        {
            ("a.example", "/x"): httpx.Response(
                200,
                json={"a": 1},
                headers={
                    "Link": '<https://a.example/x?p=2>; rel="next"',
                    "X-Total-Count": "9",
                    "Set-Cookie": "s=1",
                },
            )
        }
    )
    async with make(net, recorder=FixtureRecorder(d)) as c:
        await c.get_json("https://a.example/x")
    m = json.loads((d / "manifest.json").read_text())
    assert m["exchanges"][0]["headers"] == {
        "link": '<https://a.example/x?p=2>; rel="next"',
        "x-total-count": "9",
    }
    async with make(
        Net(),
    ) as _:
        pass
    t = FixtureTransport(d)
    async with CrawlClient(
        UA, transport=t, limiter=HostLimiter(overrides=(), default_rate=1000, default_burst=1000)
    ) as c:
        r = await c.request("GET", "https://a.example/x")
    assert r.headers["x-total-count"] == "9" and "set-cookie" not in r.headers

    # old manifest without "headers" still loads
    del m["exchanges"][0]["headers"]
    (d / "manifest.json").write_text(json.dumps(m))
    async with CrawlClient(
        UA,
        transport=FixtureTransport(d),
        limiter=HostLimiter(overrides=(), default_rate=1000, default_burst=1000),
    ) as c:
        assert (await c.request("GET", "https://a.example/x")).json() == {"a": 1}


async def test_fixture_transport_non_json_body_does_not_raise(tmp_path):
    d = tmp_path / "ad" / "tok"
    FixtureRecorder(d).record("GET", "https://a.example/x", None, 200, httpx.Headers(), b"{}")
    t = FixtureTransport(d)
    req = httpx.Request("POST", "https://a.example/x", content=b"\xff\xfenot json")
    with pytest.raises(AssertionError, match="No fixture"):
        await t.handle_async_request(req)
