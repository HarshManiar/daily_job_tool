import httpx
import pytest
import respx

from firsthand.http.client import CrawlClient
from firsthand.http.errors import (
    Blocked,
    CrawlError,
    FetchError,
    HostCircuitOpen,
    NotFound,
    ResponseTooLarge,
)
from firsthand.http.ratelimit import HostLimiter

UA = "FirsthandBot/0.1 (+https://example.invalid/bot)"
URL = "https://api.test/v1/jobs"


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make(t: FakeTime | None = None, **kw) -> CrawlClient:
    t = t or FakeTime()
    limiter = HostLimiter(
        overrides=(), default_rate=1000, default_burst=1000, clock=t.clock, sleep=t.sleep
    )
    c = CrawlClient(UA, limiter=limiter, clock=t.clock, sleep=t.sleep, **kw)
    c.t = t  # type: ignore[attr-defined]
    return c


@pytest.fixture(autouse=True)
def robots():
    with respx.mock(assert_all_called=False) as m:
        m.get(url__regex=r".*/robots\.txt").respond(404)
        yield m


async def test_ua_and_accept_headers(robots):
    route = robots.get(URL).respond(200, json={"ok": True})
    async with make() as c:
        assert await c.get_json(URL) == {"ok": True}
    h = route.calls[0].request.headers
    assert h["user-agent"] == UA
    assert h["accept"].startswith("application/json")


async def test_post_json_and_stats(robots):
    route = robots.post(URL).respond(200, json={"r": 1})
    async with make() as c:
        assert await c.post_json(URL, {"q": 1}) == {"r": 1}
        assert c.request_count == 1
        assert c.stats["api.test"].requests == 1
        assert c.stats["api.test"].bytes > 0
        c.reset_stats()
        assert c.request_count == 0 and c.stats == {}
    assert route.calls[0].request.content == b'{"q":1}'


async def test_429_retry_after_then_success(robots):
    robots.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, text="ok"),
        ]
    )
    async with make() as c:
        assert await c.get_text(URL) == "ok"
        assert c.t.sleeps == [1.0]  # type: ignore[attr-defined]


async def test_backoff_with_jitter_then_exhausted(robots):
    route = robots.get(URL).respond(503)
    async with make() as c:
        with pytest.raises(FetchError) as ei:
            await c.get_text(URL)
        sleeps = c.t.sleeps  # type: ignore[attr-defined]
    assert ei.value.status == 503
    assert route.call_count == 4
    for got, base in zip(sleeps, (2, 8, 30), strict=True):
        assert base * 0.8 <= got <= base * 1.2


async def test_timeout_retried(robots):
    robots.get(URL).mock(side_effect=[httpx.ReadTimeout("t"), httpx.Response(200, text="ok")])
    async with make() as c:
        assert await c.get_text(URL) == "ok"


async def test_network_error_exhausted(robots):
    robots.get(URL).mock(side_effect=httpx.ConnectError("x"))
    async with make(retry_delays=(0, 0)) as c:
        with pytest.raises(FetchError) as ei:
            await c.get_text(URL)
    assert ei.value.status is None


async def test_retry_after_too_long(robots):
    route = robots.get(URL).respond(429, headers={"Retry-After": "3600"})
    async with make() as c:
        with pytest.raises(FetchError):
            await c.get_text(URL)
        assert c.t.sleeps == []  # type: ignore[attr-defined]
    assert route.call_count == 1


async def test_404_not_found_no_retry(robots):
    route = robots.get(URL).respond(404)
    async with make() as c:
        with pytest.raises(NotFound):
            await c.get_text(URL)
    assert route.call_count == 1


async def test_403_blocked_no_retry(robots):
    route = robots.get(URL).respond(403)
    async with make() as c:
        with pytest.raises(Blocked) as ei:
            await c.get_text(URL)
    assert ei.value.status == 403
    assert route.call_count == 1


@pytest.mark.parametrize("status", [200, 503])
async def test_challenge_body_blocked(robots, status):
    route = robots.get(URL).respond(
        status,
        headers={"content-type": "text/html"},
        text="<title>Attention Required! | Cloudflare</title>",
    )
    async with make() as c:
        with pytest.raises(Blocked):
            await c.get_text(URL)
    assert route.call_count == 1


async def test_other_4xx_fetch_error(robots):
    robots.get(URL).respond(400)
    async with make() as c:
        with pytest.raises(FetchError) as ei:
            await c.get_text(URL)
    assert ei.value.status == 400
    assert isinstance(ei.value, CrawlError)


async def test_circuit_opens_and_closes(robots):
    robots.get(URL).respond(503)
    robots.get("https://api.test/ok").respond(200, text="ok")
    async with make(retry_delays=(0, 0, 0)) as c:
        for _ in range(3):  # 12 failed attempts
            with pytest.raises(FetchError):
                await c.get_text(URL)
        calls = c.request_count
        with pytest.raises(HostCircuitOpen):
            await c.get_text("https://api.test/ok")
        assert c.request_count == calls
        c.t.now += 30 * 60 + 1  # type: ignore[attr-defined]
        assert await c.get_text("https://api.test/ok") == "ok"


async def test_404_does_not_trip_circuit(robots):
    robots.get(URL).respond(404)
    async with make() as c:
        for _ in range(15):
            with pytest.raises(NotFound):
                await c.get_text(URL)


async def test_size_cap_streamed(robots):
    robots.get(URL).respond(200, content=b"x" * 2000)
    async with make(max_bytes=1000) as c:
        with pytest.raises(ResponseTooLarge):
            await c.get_text(URL)


async def test_cookies_not_carried(robots):
    route = robots.get(URL).mock(
        side_effect=[
            httpx.Response(200, headers={"Set-Cookie": "sid=abc; Path=/"}, text="1"),
            httpx.Response(200, text="2"),
        ]
    )
    async with make() as c:
        await c.get_text(URL)
        await c.get_text(URL)
    assert "cookie" not in route.calls[1].request.headers


async def test_recorder_fires_on_success_and_404_not_robots(robots):
    class Rec:
        def __init__(self):
            self.calls = []

        def record(self, *a):
            self.calls.append(a)

    robots.get(URL).respond(200, json={"a": 1})
    robots.get("https://api.test/missing").respond(404)
    rec = Rec()
    async with make(recorder=rec) as c:
        await c.get_json(URL, params={"b": "2"})
        with pytest.raises(NotFound):
            await c.get_text("https://api.test/missing")
    assert [(x[0], x[1], x[3]) for x in rec.calls] == [
        ("GET", URL + "?b=2", 200),
        ("GET", "https://api.test/missing", 404),
    ]


async def test_gzip_body_decoded(robots):
    import gzip

    robots.get(URL).respond(
        200, headers={"content-encoding": "gzip", "content-type": "application/json"},
        content=gzip.compress(b'{"a": 1}'),
    )  # fmt: skip
    async with make() as c:
        assert await c.get_json(URL) == {"a": 1}
