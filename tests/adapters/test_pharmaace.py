import json
from datetime import datetime

import pytest

from daily_job_tool.adapters.pharmaace import PharmaAceAdapter
from tests.adapters._helpers import fixture_client, fixture_json, json_handler, mock_client, src

A = PharmaAceAdapter()


async def test_fixture_board_only_open():
    async with fixture_client("pharmaace", "pharmaace") as c:
        res = await A.fetch_jobs(src("pharmaace", "pharmaace"), c)
    raw = fixture_json("pharmaace", "pharmaace")
    opened = [r for r in raw if r["status"] == "Open"]
    assert 0 < len(opened) < len(raw)
    assert res.complete and len(res.jobs) == len(opened)
    for j in res.jobs:
        assert j.url == f"https://pharmaace.com/career/jobs/{j.external_id}"
        assert j.title and j.locations_raw and j.posted_at.utcoffset().total_seconds() == 0
        assert json.loads(json.dumps(j.payload)) == j.payload
    j, r = res.jobs[0], opened[0]
    assert j.external_id == str(r["job_id"]) and j.title == r["position"]
    assert j.locations_raw == [r["location"]]
    assert j.department_raw == r["practice"] and j.employment_type_raw == r["position_type"]
    assert j.posted_at == datetime.fromisoformat(r["created_at"])
    assert r["description"] in j.description_html


async def test_empty_and_all_closed():
    async with mock_client(json_handler([])) as c:
        assert (await A.fetch_jobs(src("pharmaace", "x"), c)).jobs == []
    closed = [{"job_id": 1, "position": "T", "status": "Closed"}]
    async with mock_client(json_handler(closed)) as c:
        res = await A.fetch_jobs(src("pharmaace", "x"), c)
    assert res.jobs == [] and res.complete


async def test_404_raises():
    from daily_job_tool.http.errors import NotFound

    async with mock_client(json_handler({"e": 1}, status=404)) as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("pharmaace", "x"), c)


async def test_malformed_skipped():
    body = [{"job_id": 3, "position": "T", "status": "Open"}, {"status": "Open"}, "junk"]
    async with mock_client(json_handler(body)) as c:
        res = await A.fetch_jobs(src("pharmaace", "x"), c)
    assert [j.external_id for j in res.jobs] == ["3"] and len(res.warnings) == 2
    assert res.jobs[0].locations_raw == [] and res.jobs[0].description_html is None


@pytest.mark.parametrize(
    "url",
    [
        "https://pharmaace.com/career/jobs",
        "https://www.pharmaace.com/career/jobs/56",
        "https://pharmaace.com/api/V1/careers/jobs",
    ],
)
def test_match_url(url):
    assert A.match_url(url) == ("pharmaace", {})


@pytest.mark.parametrize("url", ["https://pharmaace.com/about", "https://example.com/career"])
def test_match_url_none(url):
    assert A.match_url(url) is None
