import json

import httpx
import pytest

from firsthand.http.client import CrawlClient
from firsthand.http.fixtures import FixtureRecorder, FixtureTooLarge, FixtureTransport
from firsthand.http.ratelimit import HostLimiter

UA = "FirsthandBot/0.1 (+https://example.invalid/bot)"


def fast() -> HostLimiter:
    return HostLimiter(overrides=(), default_rate=1000, default_burst=1000)


def network(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    if request.url.path == "/missing":
        return httpx.Response(404, text="nope")
    if request.method == "POST":
        return httpx.Response(200, json={"echo": json.loads(request.content)})
    return httpx.Response(200, json={"path": request.url.path, "q": dict(request.url.params)})


async def test_record_replay_round_trip(tmp_path):
    d = tmp_path / "greenhouse" / "acme"
    rec = FixtureRecorder(d, config={"x": 1})
    async with CrawlClient(
        UA, transport=httpx.MockTransport(network), recorder=rec, limiter=fast()
    ) as c:
        a = await c.get_json("https://api.test/jobs", params={"a": "1", "b": "2"})
        p = await c.post_json("https://api.test/search", {"page": 1})
        with pytest.raises(Exception):  # noqa: B017
            await c.get_json("https://api.test/missing")
    manifest = json.loads((d / "manifest.json").read_text())
    assert manifest["adapter"] == "greenhouse" and manifest["token"] == "acme"
    assert manifest["config"] == {"x": 1}
    assert [e["file"] for e in manifest["exchanges"]] == ["000.json", "001.json", "002.txt"]
    assert manifest["exchanges"][1]["json"] == {"page": 1}
    assert manifest["exchanges"][2]["status"] == 404

    async with CrawlClient(
        UA, transport=FixtureTransport(d), retry_delays=(0, 0, 0), limiter=fast()
    ) as c:
        assert await c.get_json("https://api.test/jobs?b=2&a=1") == a  # order-insensitive
        assert await c.post_json("https://api.test/search", {"page": 1}) == p


async def test_unmatched_raises(tmp_path):
    d = tmp_path / "x" / "y"
    FixtureRecorder(d).record("GET", "https://a.test/1", None, 200, httpx.Headers(), b"{}")
    async with CrawlClient(UA, transport=FixtureTransport(d), limiter=fast()) as c:
        with pytest.raises(AssertionError, match=r"https://a.test/2"):
            await c.get_json("https://a.test/2")
        with pytest.raises(AssertionError):  # same URL, different json body
            await c.post_json("https://a.test/1", {"z": 1})


async def test_multiple_dirs(tmp_path):
    for n in ("a", "b"):
        FixtureRecorder(tmp_path / n).record(
            "GET", f"https://a.test/{n}", None, 200,
            httpx.Headers({"content-type": "application/json"}), b'{"n": 1}',
        )  # fmt: skip
    t = FixtureTransport([tmp_path / "a", tmp_path / "b"])
    async with CrawlClient(UA, transport=t, limiter=fast()) as c:
        assert await c.get_json("https://a.test/a") == {"n": 1}
        assert await c.get_json("https://a.test/b") == {"n": 1}


def test_size_cap_deletes_partial(tmp_path):
    d = tmp_path / "ad" / "tok"
    rec = FixtureRecorder(d)
    rec.record("GET", "https://a.test/1", None, 200, httpx.Headers(), b"x" * 300_000)
    with pytest.raises(FixtureTooLarge):
        rec.record("GET", "https://a.test/2", None, 200, httpx.Headers(), b"x" * 300_000)
    assert sorted(p.name for p in d.iterdir()) == ["000.txt", "manifest.json"]
    # fresh dir: nothing left behind
    d2 = tmp_path / "ad" / "big"
    with pytest.raises(FixtureTooLarge):
        FixtureRecorder(d2).record(
            "GET", "https://a.test/1", None, 200, httpx.Headers(), b"x" * 600_000
        )
    assert not d2.exists()


def test_rerecord_same_exchange_replaces(tmp_path):
    rec = FixtureRecorder(tmp_path / "a" / "b")
    for body in (b"1", b"2"):
        rec.record("GET", "https://a.test/1?x=1&y=2", None, 200, httpx.Headers(), body)
    m = json.loads(rec.manifest_path.read_text())
    assert len(m["exchanges"]) == 1
    assert (tmp_path / "a" / "b" / "000.txt").read_bytes() == b"2"
