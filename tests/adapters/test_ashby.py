import json
from datetime import datetime

import pytest

from daily_job_tool.adapters.ashby import AshbyAdapter
from daily_job_tool.http.errors import NotFound
from tests.adapters._helpers import fixture_client, fixture_json, json_handler, mock_client, src

A = AshbyAdapter()
BOARDS = ["resend", "railway", "posthog"]


@pytest.mark.parametrize("token", BOARDS)
async def test_fixture_boards(token):
    async with fixture_client("ashby", token) as c:
        res = await A.fetch_jobs(src("ashby", token), c)
    raw = [r for r in fixture_json("ashby", token)["jobs"] if r.get("isListed") is not False]
    assert res.complete and len(res.jobs) == len(raw) > 0
    ids = [j.external_id for j in res.jobs]
    assert len(set(ids)) == len(ids)
    for j in res.jobs:
        assert j.external_id and j.title and j.url
        assert json.loads(json.dumps(j.payload)) == j.payload
    j, r = res.jobs[0], raw[0]
    assert j.external_id == r["id"] and j.title == r["title"]
    assert j.url == r["jobUrl"] and j.apply_url == r["applyUrl"]
    assert j.locations_raw[0] == r["location"]
    assert j.department_raw == (r.get("department") or r.get("team"))
    assert j.employment_type_raw == r.get("employmentType")
    assert j.workplace_raw == r.get("workplaceType")
    assert j.description_html == r["descriptionHtml"]
    assert j.posted_at == datetime.fromisoformat(r["publishedAt"])
    assert j.posted_at.utcoffset().total_seconds() == 0
    assert j.payload == r


async def test_fixture_compensation_mapped_when_present():
    for token in BOARDS:
        async with fixture_client("ashby", token) as c:
            res = await A.fetch_jobs(src("ashby", token), c)
        raw = {r["id"]: r for r in fixture_json("ashby", token)["jobs"]}
        for j in res.jobs:
            comp = raw[j.external_id].get("compensation") or {}
            if comp.get("summaryComponents") or comp.get("compensationTierSummary"):
                assert j.salary is not None


async def test_404_raises():
    async with fixture_client("ashby", "deno") as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("ashby", "deno"), c)


async def test_empty_board():
    async with mock_client(json_handler({"jobs": []})) as c:
        res = await A.fetch_jobs(src("ashby", "x"), c)
    assert res.jobs == [] and res.complete


def _job(**kw):
    base = {"id": "1", "title": "T", "jobUrl": "https://jobs.ashbyhq.com/x/1", "isListed": True}
    return {**base, **kw}


async def test_synthetic_compensation_unlisted_remote_malformed():
    comp = {
        "compensationTierSummary": "$100K - $150K",
        "summaryComponents": [
            {
                "compensationType": "Salary",
                "interval": "1 YEAR",
                "currencyCode": "USD",
                "minValue": 100000,
                "maxValue": 150000,
            },
            {"compensationType": "EquityPercentage", "minValue": 1, "maxValue": 2},
        ],
    }
    body = {
        "jobs": [
            _job(
                compensation=comp,
                isRemote=True,
                location="NYC",
                secondaryLocations=[{"location": "SF"}, {"location": "NYC"}],
            ),
            _job(id="2", isListed=False),
            {"id": "3", "title": "no url"},
        ]
    }
    async with mock_client(json_handler(body)) as c:
        res = await A.fetch_jobs(src("ashby", "x"), c)
    assert len(res.jobs) == 1 and len(res.warnings) == 1
    j = res.jobs[0]
    s = j.salary
    assert (s.min, s.max, s.currency, s.period, s.text) == (
        100000,
        150000,
        "USD",
        "1 YEAR",
        "$100K - $150K",
    )
    assert j.workplace_raw == "Remote"
    assert j.locations_raw == ["NYC", "SF"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jobs.ashbyhq.com/PostHog", "PostHog"),
        ("https://jobs.ashbyhq.com/resend/abc-123/application", "resend"),
        (
            "https://api.ashbyhq.com/posting-api/job-board/railway?includeCompensation=true",
            "railway",
        ),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == (expected, {})


@pytest.mark.parametrize(
    "url",
    [
        "https://ashbyhq.com/x",
        "https://jobs.ashbyhq.com/",
        "https://api.ashbyhq.com/other/x",
        "https://example.com/a",
    ],
)
def test_match_url_none(url):
    assert A.match_url(url) is None
