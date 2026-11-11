import json
from datetime import UTC, datetime

import pytest

from daily_job_tool.adapters.workable import WorkableAdapter
from daily_job_tool.http.errors import NotFound
from tests.adapters._helpers import fixture_client, fixture_json, json_handler, mock_client, src

A = WorkableAdapter()
TOKEN = "innovaccer-analytics"


async def test_fixture_board():
    async with fixture_client("workable", TOKEN) as c:
        res = await A.fetch_jobs(src("workable", TOKEN), c)
    raw = fixture_json("workable", TOKEN)["jobs"]
    codes = {r["shortcode"] for r in raw}
    assert res.complete and len(raw) == 25 and len(res.jobs) == len(codes) == 20
    assert res.board_name == "Innovaccer Analytics"
    multi = next(j for j in res.jobs if j.external_id == "3C3F6CE986")
    assert len(multi.locations_raw) == 6  # one widget row per location, merged
    for j in res.jobs:
        assert j.title and j.url and j.locations_raw and j.description_html
        assert j.posted_at.tzinfo is not None and j.posted_at.utcoffset().total_seconds() == 0
        assert json.loads(json.dumps(j.payload)) == j.payload
        assert not j.needs_detail
    j, r = res.jobs[0], raw[0]
    assert r["shortcode"] == j.external_id
    assert j.external_id == r["shortcode"] and j.url == r["url"]
    assert j.apply_url == r["application_url"]
    assert j.department_raw == r["department"]
    assert j.posted_at == datetime.fromisoformat(r["published_on"]).replace(tzinfo=UTC)
    us = [x for x in res.jobs if any("United States" in loc for loc in x.locations_raw)]
    assert us


async def test_404_raises():
    async with fixture_client("workable", "nonexistent-co") as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("workable", "nonexistent-co-zz"), c)


async def test_empty_board():
    async with mock_client(json_handler({"name": "X", "jobs": []})) as c:
        res = await A.fetch_jobs(src("workable", "x"), c)
    assert res.jobs == [] and res.complete


async def test_synthetic_malformed_remote_hidden_location():
    body = {
        "jobs": [
            {
                "shortcode": "AB",
                "title": "T",
                "url": "https://apply.workable.com/j/AB",
                "telecommuting": True,
                "employment_type": "Full-time",
                "published_on": "2026-10-01",
                "locations": [
                    {"city": "Austin", "region": "Texas", "country": "United States"},
                    {"city": "Hidden", "country": "X", "hidden": True},
                ],
            },
            {"title": "no shortcode"},
            "junk",
        ]
    }
    async with mock_client(json_handler(body)) as c:
        res = await A.fetch_jobs(src("workable", "x"), c)
    assert len(res.jobs) == 1 and len(res.warnings) == 2
    j = res.jobs[0]
    assert j.locations_raw == ["Austin, Texas, United States"]
    assert j.workplace_raw == "Remote" and j.employment_type_raw == "Full-time"
    assert j.posted_at == datetime(2026, 10, 1, tzinfo=UTC)


async def test_fallback_flat_location():
    rec = {"shortcode": "A", "title": "T", "url": "u", "city": "Noida", "country": "India"}
    assert A._map(rec).locations_raw == ["Noida, India"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://apply.workable.com/innovaccer-analytics/", "innovaccer-analytics"),
        ("https://apply.workable.com/Acme/j/8EE4D3922D/", "Acme"),
        ("https://apply.workable.com/api/v1/widget/accounts/acme?details=true", "acme"),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == (expected, {})


@pytest.mark.parametrize(
    "url",
    [
        "https://apply.workable.com/",
        "https://apply.workable.com/j/8EE4D3922D",
        "https://workable.com/acme",
        "https://example.com/a",
    ],
)
def test_match_url_none(url):
    assert A.match_url(url) is None
