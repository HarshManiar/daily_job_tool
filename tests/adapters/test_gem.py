import json
from datetime import datetime

import pytest

from daily_job_tool.adapters.gem import GemAdapter
from daily_job_tool.http.errors import NotFound
from tests.adapters._helpers import fixture_client, fixture_json, json_handler, mock_client, src

A = GemAdapter()


async def test_fixture_board():
    async with fixture_client("gem", "mindbloom") as c:
        res = await A.fetch_jobs(src("gem", "mindbloom"), c)
    raw = fixture_json("gem", "mindbloom")
    kept = [r for r in raw if "don't see your role" not in r["title"].lower()]
    assert res.complete and len(res.jobs) == len(kept) > 0
    for j in res.jobs:
        assert j.external_id and j.title and j.url.startswith("https://jobs.gem.com/mindbloom/")
        assert j.locations_raw and j.posted_at.utcoffset().total_seconds() == 0
        assert json.loads(json.dumps(j.payload)) == j.payload
    j, r = res.jobs[0], kept[0]
    assert j.external_id == r["id"] and j.url == r["absolute_url"]
    assert j.employment_type_raw == r["employment_type"]
    assert j.workplace_raw == r["location_type"]
    assert j.locations_raw[0] == r["location"]["name"]
    assert j.description_html == r["content"]
    assert j.posted_at == datetime.fromisoformat(r["first_published_at"])
    assert j.department_raw == r["departments"][0]["name"]


async def test_404_raises():
    async with fixture_client("gem", "nonexistent-co") as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("gem", "nonexistent-co-zz"), c)


async def test_empty_board():
    async with mock_client(json_handler([])) as c:
        res = await A.fetch_jobs(src("gem", "x"), c)
    assert res.jobs == [] and res.complete


async def test_malformed_and_catch_all():
    ok = {"id": "1", "title": "T", "absolute_url": "https://jobs.gem.com/x/1"}
    body = [
        ok,
        {"id": "2", "title": "Don’t See Your Role? Apply Here!", "absolute_url": "u"},
        {"id": "3"},
        7,
    ]
    async with mock_client(json_handler(body)) as c:
        res = await A.fetch_jobs(src("gem", "x"), c)
    assert [j.external_id for j in res.jobs] == ["1"] and len(res.warnings) == 2
    assert res.jobs[0].posted_at is None and res.jobs[0].locations_raw == []


async def test_non_list_payload_raises():
    async with mock_client(json_handler({"oops": 1})) as c:
        with pytest.raises(ValueError):
            await A.fetch_jobs(src("gem", "x"), c)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jobs.gem.com/mindbloom", "mindbloom"),
        ("https://jobs.gem.com/mindbloom/abc123", "mindbloom"),
        ("https://api.gem.com/job_board/v0/mindbloom/job_posts/", "mindbloom"),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == (expected, {})


@pytest.mark.parametrize(
    "url", ["https://jobs.gem.com/", "https://api.gem.com/other/x", "https://gem.com/a"]
)
def test_match_url_none(url):
    assert A.match_url(url) is None
