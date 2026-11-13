import json
from datetime import UTC, datetime

import httpx
import pytest

from daily_job_tool.adapters.bamboohr import BambooHRAdapter, parse_compensation
from daily_job_tool.http.errors import NotFound
from daily_job_tool.pipeline.ingest import payload_hash
from tests.adapters._helpers import fixture_client, fixture_json, json_handler, mock_client, src

A = BambooHRAdapter()


@pytest.mark.parametrize(("token", "n"), [("pharmaforceiq", 9), ("berkshiregrey", 20)])
async def test_fixture_boards(token, n):
    async with fixture_client("bamboohr", token) as c:
        res = await A.fetch_jobs(src("bamboohr", token), c)
    assert res.complete and len(res.jobs) == n
    ids = [j.external_id for j in res.jobs]
    assert len(set(ids)) == n
    for j in res.jobs:
        assert j.title and j.url == f"https://{token}.bamboohr.com/careers/{j.external_id}"
        assert j.needs_detail and j.description_html is None
        assert json.loads(json.dumps(j.payload)) == j.payload
    assert res.jobs[0].locations_raw


async def test_detail_maps_pay_date_description():
    s = src("bamboohr", "pharmaforceiq")
    async with fixture_client("bamboohr", "pharmaforceiq") as c:
        res = await A.fetch_jobs(s, c)
        job = next(j for j in res.jobs if j.external_id == "29")
        d = await A.fetch_detail(s, job, c)
    assert d.external_id == "29" and not d.needs_detail
    assert d.description_html and "About Us" in d.description_html
    assert d.posted_at == datetime(2026, 5, 11, tzinfo=UTC)
    assert (d.salary.min, d.salary.max, d.salary.period) == (165000, 175000, "year")
    assert d.locations_raw == ["Chester, Pennsylvania, United States"]
    assert d.employment_type_raw == "Full-Time"
    assert "formFields" not in json.dumps(d.payload)
    assert A._map(d.payload) == d


async def test_detail_404():
    s = src("bamboohr", "pharmaforceiq")
    async with fixture_client("bamboohr", "pharmaforceiq") as c:
        job = (await A.fetch_jobs(s, c)).jobs[0].model_copy(update={"external_id": "999999"})
        with pytest.raises(NotFound):
            await A.fetch_detail(s, job, c)


async def test_stable_payload_and_empty_and_malformed():
    s = src("bamboohr", "pharmaforceiq")
    async with fixture_client("bamboohr", "pharmaforceiq") as c:
        a = await A.fetch_jobs(s, c)
        b = await A.fetch_jobs(s, c)
    assert [payload_hash(j.payload) for j in a.jobs] == [payload_hash(j.payload) for j in b.jobs]
    async with mock_client(json_handler({"meta": {}, "result": []})) as c:
        res = await A.fetch_jobs(s, c)
    assert res.jobs == [] and res.complete
    body = {"result": [{"id": "1", "jobOpeningName": "T", "isRemote": True}, {"id": "2"}, 5]}
    async with mock_client(json_handler(body)) as c:
        res = await A.fetch_jobs(s, c)
    assert len(res.jobs) == 1 and len(res.warnings) == 2
    assert res.jobs[0].workplace_raw == "remote"


async def test_bad_payload_raises():
    async with mock_client(json_handler({"nope": 1})) as c:
        with pytest.raises(ValueError):
            await A.fetch_jobs(src("bamboohr", "x"), c)


def test_berkshire_detail_fixture_shape():
    d = fixture_json("bamboohr", "berkshiregrey", "001.json")
    job = A._map(
        {
            "id": "689",
            "jobOpeningName": "T",
            "_co": "berkshiregrey",
            "detail": d["result"]["jobOpening"],
        }
    )
    assert job.locations_raw == ["Bedford, Massachusetts, United States"]
    assert (job.salary.min, job.salary.max) == (150000, 230000)


@pytest.mark.parametrize(
    ("text", "lo", "hi", "per"),
    [("$20 - $30 per hour", 20, 30, "hour"), ("Competitive", None, None, None)],
)
def test_compensation(text, lo, hi, per):
    s = parse_compensation(text)
    assert (s.min, s.max, s.period, s.text) == (lo, hi, per, text)
    assert parse_compensation(None) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://berkshiregrey.bamboohr.com/careers/689", ("berkshiregrey", {})),
        ("https://pharmaforceiq.bamboohr.com/careers", ("pharmaforceiq", {})),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == expected


@pytest.mark.parametrize(
    "url", ["https://www.bamboohr.com/", "https://bamboohr.com/x", "https://example.com/careers"]
)
def test_match_url_none(url):
    assert A.match_url(url) is None


def test_httpx_unused():
    assert httpx
