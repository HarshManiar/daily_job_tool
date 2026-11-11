import json
from datetime import UTC, datetime

import pytest

from firsthand.adapters.lever import LeverAdapter
from firsthand.http.errors import NotFound
from tests.adapters._helpers import fixture_client, fixture_json, json_handler, mock_client, src

A = LeverAdapter()


@pytest.mark.parametrize("token", ["weride", "tala"])
async def test_fixture_boards(token):
    async with fixture_client("lever", token) as c:
        res = await A.fetch_jobs(src("lever", token), c)
    raw = fixture_json("lever", token)
    assert res.complete and len(res.jobs) == len(raw) > 0
    ids = [j.external_id for j in res.jobs]
    assert len(set(ids)) == len(ids)
    for j in res.jobs:
        assert j.external_id and j.title and j.url
        assert json.loads(json.dumps(j.payload)) == j.payload
    j, r = res.jobs[0], raw[0]
    assert j.external_id == r["id"] and j.title == r["text"]
    assert j.url == r["hostedUrl"] and j.apply_url == r["applyUrl"]
    assert j.locations_raw[0] == r["categories"]["location"]
    assert j.employment_type_raw == r["categories"].get("commitment")
    assert j.workplace_raw == r.get("workplaceType")
    assert j.posted_at == datetime.fromtimestamp(r["createdAt"] / 1000, UTC)
    assert j.description_html.startswith(r["description"])
    assert j.description_html.endswith(r["additional"])
    for lst in r["lists"]:
        assert lst["text"] in j.description_html and lst["content"] in j.description_html
    assert j.payload == r


async def test_salary_mapped():
    async with fixture_client("lever", "tala") as c:
        res = await A.fetch_jobs(src("lever", "tala"), c)
    raw = {r["id"]: r for r in fixture_json("lever", "tala")}
    with_sal = [j for j in res.jobs if raw[j.external_id].get("salaryRange")]
    assert with_sal
    s, r = with_sal[0].salary, raw[with_sal[0].external_id]["salaryRange"]
    assert (s.min, s.max, s.currency, s.period) == (
        r["min"],
        r["max"],
        r["currency"],
        r["interval"],
    )


async def test_404_raises():
    async with fixture_client("lever", "zzz-no-such-board-firsthand") as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("lever", "zzz-no-such-board-firsthand"), c)


async def test_empty_board():
    async with mock_client(json_handler([])) as c:
        res = await A.fetch_jobs(src("lever", "x"), c)
    assert res.jobs == [] and res.complete


async def test_eu_region_host():
    hosts = []

    def handler(request):
        import httpx

        hosts.append(request.url.host)
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, json=[])
        )

    async with mock_client(handler) as c:
        await A.fetch_jobs(src("lever", "x", region="eu"), c)
    assert "api.eu.lever.co" in hosts


async def test_malformed_skipped_and_locations():
    good = {
        "id": "a",
        "text": "T",
        "hostedUrl": "https://jobs.lever.co/x/a",
        "createdAt": 1700000000000,
        "categories": {"location": "Berlin", "allLocations": ["Berlin", "Paris"], "team": "Eng"},
    }
    async with mock_client(json_handler([good, {"id": "b", "text": "no url"}])) as c:
        res = await A.fetch_jobs(src("lever", "x"), c)
    assert len(res.jobs) == 1 and len(res.warnings) == 1
    assert res.jobs[0].locations_raw == ["Berlin", "Paris"]
    assert res.jobs[0].department_raw == "Eng"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jobs.lever.co/weride", ("weride", {})),
        ("https://jobs.lever.co/weride/012c818d/apply", ("weride", {})),
        ("https://jobs.eu.lever.co/acme/abc", ("acme", {"region": "eu"})),
        ("https://api.lever.co/v0/postings/tala?mode=json", ("tala", {})),
        ("https://api.eu.lever.co/v0/postings/acme", ("acme", {"region": "eu"})),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://lever.co/weride",
        "https://jobs.lever.co/",
        "https://example.com/x",
        "https://boards.greenhouse.io/x",
    ],
)
def test_match_url_none(url):
    assert A.match_url(url) is None
