import json
from datetime import datetime

import httpx
import pytest

from daily_job_tool.adapters.rippling import RipplingAdapter
from daily_job_tool.http.errors import NotFound
from tests.adapters._helpers import fixture_client, fixture_json, json_handler, mock_client, src

A = RipplingAdapter()
TOKEN = "carbon-health"
FIRST = "b34362b9-43df-45aa-bc0b-4d5807f61f65"


async def test_fixture_list_dedup_and_merge():
    async with fixture_client("rippling", TOKEN) as c:
        res = await A.fetch_jobs(src("rippling", TOKEN), c)
    rows = fixture_json("rippling", TOKEN)
    uuids = {r["uuid"] for r in rows}
    assert res.complete and len(res.jobs) == len(uuids) == 25 < len(rows)
    j = res.jobs[0]
    assert j.external_id == FIRST and j.needs_detail and j.posted_at is None
    assert len(j.locations_raw) == len(set(j.locations_raw)) > 1
    assert j.department_raw == "Clinicians" and j.url.endswith(FIRST)
    assert json.loads(json.dumps(j.payload)) == j.payload


async def test_fetch_detail_fills_date_description_and_remaps():
    async with fixture_client("rippling", TOKEN) as c:
        res = await A.fetch_jobs(src("rippling", TOKEN), c)
        j = await A.fetch_detail(src("rippling", TOKEN), res.jobs[0], c)
    assert not j.needs_detail
    assert j.posted_at == datetime.fromisoformat("2026-07-22T11:43:35.834000-07:00")
    assert j.posted_at.utcoffset().total_seconds() == 0
    assert j.employment_type_raw == "Salaried, full-time"
    assert "<" in j.description_html
    assert j.title == res.jobs[0].title
    assert A._map(j.payload) == j  # renormalize path reproduces the detailed job


async def test_detail_only_for_new_postings():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json=[])

    async with mock_client(handler) as c:
        res = await A.fetch_jobs(src("rippling", "x"), c)
    assert res.jobs == []
    assert [p for p in calls if p != "/robots.txt"] == ["/platform/api/ats/v1/board/x/jobs"]


async def test_detail_without_next_data_is_tolerated():
    job = A._map({"uuid": "u", "name": "T", "url": "https://ats.rippling.com/x/jobs/u"})

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html>nothing</html>")

    async with mock_client(handler) as c:
        out = await A.fetch_detail(src("rippling", "x"), job, c)
    assert not out.needs_detail and out.posted_at is None


async def test_404_raises():
    async with fixture_client("rippling", "nonexistent-co") as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("rippling", "nonexistent-co-zz"), c)


async def test_empty_and_malformed():
    async with mock_client(json_handler([])) as c:
        res = await A.fetch_jobs(src("rippling", "x"), c)
    assert res.jobs == [] and res.complete
    body = [
        {"uuid": "1", "name": "T", "url": "u", "workLocation": {"label": "NYC"}},
        {"uuid": "1", "name": "T", "url": "u", "workLocation": {"label": "SF"}},
        {"uuid": "2"},
        {"uuid": "3", "name": "no url"},
        5,
    ]
    async with mock_client(json_handler(body)) as c:
        res = await A.fetch_jobs(src("rippling", "x"), c)
    assert len(res.jobs) == 1 and res.jobs[0].locations_raw == ["NYC", "SF"]
    assert len(res.warnings) == 3


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://ats.rippling.com/carbon-health/jobs", "carbon-health"),
        ("https://ats.rippling.com/carbon-health/jobs/b34362b9", "carbon-health"),
        ("https://ats.rippling.com/embed/carbon-health/jobs", "carbon-health"),
        ("https://api.rippling.com/platform/api/ats/v1/board/carbon-health/jobs", "carbon-health"),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == (expected, {})


@pytest.mark.parametrize(
    "url",
    [
        "https://ats.rippling.com/",
        "https://ats.rippling.com/internal/x",
        "https://api.rippling.com/platform/api/other",
        "https://rippling.com/a",
    ],
)
def test_match_url_none(url):
    assert A.match_url(url) is None
