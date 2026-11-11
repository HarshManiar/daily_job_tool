import html
import json
from datetime import UTC, datetime

import httpx
import pytest

from firsthand.adapters.greenhouse import GreenhouseAdapter
from firsthand.http.errors import NotFound
from tests.adapters._helpers import fixture_client, fixture_json, json_handler, mock_client, src

A = GreenhouseAdapter()


@pytest.mark.parametrize("token", ["warp", "airtable", "discord"])
async def test_fixture_boards(token):
    async with fixture_client("greenhouse", token) as c:
        res = await A.fetch_jobs(src("greenhouse", token), c)
    raw = fixture_json("greenhouse", token)["jobs"]
    assert res.complete and len(res.jobs) == len(raw) > 0
    ids = [j.external_id for j in res.jobs]
    assert len(set(ids)) == len(ids)
    for j in res.jobs:
        assert j.external_id and j.title and j.url
        assert json.loads(json.dumps(j.payload)) == j.payload
    j, r = res.jobs[0], raw[0]
    assert j.external_id == str(r["id"])
    assert j.title == r["title"]
    assert j.url == j.apply_url == r["absolute_url"]
    assert j.locations_raw == [r["location"]["name"].strip()]
    assert j.department_raw == r["departments"][0]["name"]
    assert j.description_html == html.unescape(r["content"])
    assert "&lt;" not in j.description_html
    assert j.updated_at == datetime.fromisoformat(r["updated_at"]).astimezone(UTC)
    assert j.posted_at == datetime.fromisoformat(r["first_published"]).astimezone(UTC)
    assert j.updated_at.tzinfo is not None
    assert j.payload == r


async def test_board_name_from_fixture():
    async with fixture_client("greenhouse", "warp") as c:
        res = await A.fetch_jobs(src("greenhouse", "warp"), c)
    assert res.board_name == "Warp"


async def test_board_name_failure_tolerated():
    async with fixture_client("greenhouse", "airtable") as c:  # board info not recorded
        res = await A.fetch_jobs(src("greenhouse", "airtable"), c)
    assert res.board_name is None and res.complete
    assert any("board name" in w for w in res.warnings)


async def test_404_raises():
    async with fixture_client("greenhouse", "notion") as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("greenhouse", "notion"), c)


async def test_empty_board():
    async with mock_client(json_handler({"jobs": []})) as c:
        res = await A.fetch_jobs(src("greenhouse", "x"), c)
    assert res.jobs == [] and res.complete


async def test_malformed_skipped():
    good = {"id": 1, "title": "T", "absolute_url": "https://x/1", "content": "&lt;p&gt;hi"}
    body = {"jobs": [good, {"id": 2, "title": "no url"}, {"title": "no id", "absolute_url": "u"}]}
    async with mock_client(json_handler(body)) as c:
        res = await A.fetch_jobs(src("greenhouse", "x"), c)
    assert [j.external_id for j in res.jobs] == ["1"]
    assert sum("skipped" in w for w in res.warnings) == 2
    assert res.jobs[0].description_html == "<p>hi"


async def test_fetch_detail_pay_ranges():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "id": 1,
                "pay_input_ranges": [
                    {
                        "min_cents": 20000000,
                        "max_cents": 22500000,
                        "currency_type": "USD",
                        "title": "Base Salary",
                    }
                ],
            },
        )

    list_body = {"jobs": [{"id": 1, "title": "T", "absolute_url": "https://x/1"}]}
    async with mock_client(json_handler(list_body)) as c:
        job = (await A.fetch_jobs(src("greenhouse", "x"), c)).jobs[0]
    assert job.needs_detail and job.salary is None
    async with mock_client(handler) as c:
        out = await A.fetch_detail(src("greenhouse", "x"), job, c)
    assert "pay_transparency=true" in seen[0] and "/jobs/1" in seen[0]
    assert out.salary is not None
    assert (out.salary.min, out.salary.max, out.salary.currency) == (200000, 225000, "USD")
    assert not out.needs_detail
    assert out.payload["pay_input_ranges"][0]["min_cents"] == 20000000


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/warp", "warp"),
        ("https://boards.greenhouse.io/warp/jobs/123", "warp"),
        ("https://job-boards.greenhouse.io/airtable/jobs/9?gh_jid=9", "airtable"),
        ("https://boards-api.greenhouse.io/v1/boards/discord/jobs?content=true", "discord"),
        ("https://boards.greenhouse.io/embed/job_board?for=notion", "notion"),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == (expected, {})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/warp",
        "https://greenhouse.io/warp",
        "https://boards.greenhouse.io/",
        "https://boards-api.greenhouse.io/",
        "https://jobs.lever.co/warp",
        "not a url",
    ],
)
def test_match_url_none(url):
    assert A.match_url(url) is None
