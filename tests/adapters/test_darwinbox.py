import json
from datetime import UTC, datetime

import httpx
import pytest

from daily_job_tool.adapters.darwinbox import DarwinboxAdapter
from daily_job_tool.http.errors import NotFound
from tests.adapters._helpers import fixture_client, fixture_json, mock_client, src

A = DarwinboxAdapter()


def _job(i, **kw):
    return {"id": f"j{i}", "title": f"Job {i}", "tool_tip_locations": ["Austin, Texas"], **kw}


def _pages(pages, fail_page=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        n = int(request.url.params["page"])
        if n == fail_page:
            return httpx.Response(404, text="gone")
        jobs = pages[n - 1] if n <= len(pages) else []
        return httpx.Response(200, json={"status": "success", "message": {"jobs": jobs}})

    return handler


async def test_fixture_board():
    async with fixture_client("darwinbox", "transcarent") as c:
        # fixture holds page 1 (10 jobs) and an empty page 4; page 2 is unrecorded -> not used
        with pytest.raises(AssertionError):
            await A.fetch_jobs(src("darwinbox", "transcarent"), c)


def test_map_fixture_records():
    raw = fixture_json("darwinbox", "transcarent")["message"]["jobs"]
    jobs = [A._map(r, "transcarent.darwinbox.com") for r in raw]
    assert all(jobs) and len(jobs) == 10
    j, r = jobs[0], raw[0]
    assert j.external_id == r["id"] and j.title == r["title"]
    assert (
        j.url
        == f"https://transcarent.darwinbox.com/ms/candidatev2/main/careers/jobDetails/{r['id']}"
    )
    assert j.locations_raw == r["tool_tip_locations"]
    assert j.department_raw == r["department"] and j.employment_type_raw == r["emp_type"]
    assert j.posted_at == datetime.fromtimestamp(r["job_posting_on"], UTC)
    assert json.loads(json.dumps(j.payload)) == j.payload
    # renormalize path: _map(payload) rebuilds the same job
    assert A._map(j.payload) == j


def test_created_on_fallback_and_title_fallback():
    j = A._map(
        {"id": "1", "designation_display_name": "D", "created_on": "2026-09-01T20:57:03.000Z"},
        "h.darwinbox.com",
    )
    assert j.title == "D" and j.posted_at == datetime(2026, 9, 1, 20, 57, 3, tzinfo=UTC)


async def test_pagination_until_empty():
    pages = [[_job(i) for i in range(10)], [_job(i) for i in range(10, 20)], [_job(20)]]
    async with mock_client(_pages(pages)) as c:
        res = await A.fetch_jobs(src("darwinbox", "acme"), c)
    assert res.complete and len(res.jobs) == 21
    assert res.jobs[0].url.startswith("https://acme.darwinbox.com/")


async def test_config_host_used():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return _pages([[_job(1)]])(request)

    async with mock_client(handler) as c:
        await A.fetch_jobs(src("t", "tok", host="custom.darwinbox.com"), c)
    assert set(seen) == {"custom.darwinbox.com"}


async def test_failure_mid_way_is_incomplete():
    pages = [[_job(i) for i in range(10)], [_job(10)]]
    async with mock_client(_pages(pages, fail_page=2)) as c:
        res = await A.fetch_jobs(src("darwinbox", "acme"), c)
    assert not res.complete and len(res.jobs) == 10
    assert any("page 2" in w for w in res.warnings)


async def test_first_page_failure_raises():
    async with mock_client(_pages([], fail_page=1)) as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("darwinbox", "acme"), c)


async def test_empty_board_and_repeated_page_guard():
    async with mock_client(_pages([])) as c:
        res = await A.fetch_jobs(src("darwinbox", "x"), c)
    assert res.jobs == [] and res.complete

    def repeating(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"message": {"jobs": [_job(1)]}})

    async with mock_client(repeating) as c:
        res = await A.fetch_jobs(src("darwinbox", "x"), c)
    assert len(res.jobs) == 1 and res.complete


async def test_malformed_record_skipped():
    pages = [[_job(1), {"id": "x"}, "junk"]]
    async with mock_client(_pages(pages)) as c:
        res = await A.fetch_jobs(src("darwinbox", "x"), c)
    assert len(res.jobs) == 1 and len(res.warnings) == 2


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://transcarent.darwinbox.com/ms/candidatev2/main/careers/allJobs", "transcarent"),
        ("https://Acme.darwinbox.com/", "acme"),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == (expected, {})


@pytest.mark.parametrize(
    "url",
    [
        "https://darwinbox.com/",
        "https://www.darwinbox.com/x",
        "https://a.b.darwinbox.com/",
        "https://x.com/",
    ],
)
def test_match_url_none(url):
    assert A.match_url(url) is None
