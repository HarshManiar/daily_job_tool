import httpx
import respx

from firsthand.http.client import CrawlClient
from firsthand.http.errors import RobotsDisallowed
from firsthand.http.ratelimit import HostLimiter
from firsthand.http.robots import InMemoryRobotsCache

UA = "FirsthandBot/0.1 (+https://example.invalid/bot)"


def make() -> CrawlClient:
    return CrawlClient(
        UA,
        limiter=HostLimiter(overrides=(), default_rate=1000, default_burst=1000),
        retry_delays=(0, 0, 0),
    )


@respx.mock
async def test_allow_and_deny():
    respx.get("https://x.test/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /private\n\nUser-agent: FirsthandBot\nDisallow: /nope\n"
    )
    ok = respx.get("https://x.test/jobs").respond(200, json={"a": 1})
    async with make() as c:
        assert await c.get_json("https://x.test/jobs") == {"a": 1}
        import pytest

        with pytest.raises(RobotsDisallowed):
            await c.get_text("https://x.test/nope/1")
        # specific group wins over '*'
        assert await c.request("GET", "https://x.test/jobs")
    assert ok.called


@respx.mock
async def test_robots_404_allows():
    respx.get("https://x.test/robots.txt").respond(404)
    respx.get("https://x.test/a").respond(200, text="hi")
    async with make() as c:
        assert await c.get_text("https://x.test/a") == "hi"


@respx.mock
async def test_robots_500_disallows():
    import pytest

    respx.get("https://x.test/robots.txt").respond(500)
    a = respx.get("https://x.test/a").respond(200, text="hi")
    async with make() as c:
        with pytest.raises(RobotsDisallowed):
            await c.get_text("https://x.test/a")
    assert not a.called


@respx.mock
async def test_robots_network_error_disallows():
    import pytest

    respx.get("https://x.test/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
    async with make() as c:
        with pytest.raises(RobotsDisallowed):
            await c.get_text("https://x.test/a")


@respx.mock
async def test_robots_cached():
    r = respx.get("https://x.test/robots.txt").respond(200, text="User-agent: *\nAllow: /\n")
    respx.get(url__startswith="https://x.test/p").respond(200, text="ok")
    async with make() as c:
        await c.get_text("https://x.test/p1")
        await c.get_text("https://x.test/p2")
    assert r.call_count == 1


@respx.mock
async def test_robots_sent_with_ua_and_crawl_delay():
    r = respx.get("https://x.test/robots.txt").respond(200, text="User-agent: *\nCrawl-delay: 4\n")
    respx.get("https://x.test/p").respond(200, text="ok")
    async with make() as c:
        await c.get_text("https://x.test/p")
        assert c._limiter.rate_for("x.test") == 0.25
    assert r.calls[0].request.headers["user-agent"] == UA


async def test_cache_ttls():
    now = [0.0]
    cache = InMemoryRobotsCache(lambda: now[0])
    await cache.put("a", 200, "x")
    await cache.put("b", 500, "")
    now[0] = 3601
    assert await cache.get("a") == (200, "x")
    assert await cache.get("b") is None
    now[0] = 24 * 3600 + 1
    assert await cache.get("a") is None
