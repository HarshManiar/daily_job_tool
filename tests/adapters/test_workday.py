import json
import re
from datetime import UTC, date, datetime

import httpx
import pytest

from daily_job_tool.adapters.base import RawJob
from daily_job_tool.adapters.workday import (
    DEFAULT_DETAIL_TITLE_REGEX,
    US_COUNTRY_ID,
    WorkdayAdapter,
    find_us_facet,
    parse_relative,
)
from daily_job_tool.http.errors import NotFound
from tests.adapters._helpers import fixture_client, fixture_json, mock_client, src

A = WorkdayAdapter()
CYTO = dict(host="cytokinetics.wd1.myworkdayjobs.com", tenant="cytokinetics", site="Cytokinetics")
IAS = dict(host="integralads.wd1.myworkdayjobs.com", tenant="integralads", site="IAScareers")


def s(token, **cfg):
    return src("workday", token, **cfg)


def mk(n, title="Analyst", off=0):
    return {
        "title": f"{title} {off + n}",
        "externalPath": f"/job/Remote/x-{off + n}_R{off + n}",
        "locationsText": "Remote",
        "postedOn": "Posted Today",
        "bulletFields": [f"R{off + n}"],
    }


def server(total_pages, *, total_on_later=True, facets=None, log=None, size=20):
    """Mock Workday list endpoint serving total_pages pages of `size` jobs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = json.loads(request.content)
        if log is not None:
            log.append(body)
        off = body["offset"]
        n = max(0, min(size, total_pages * size - off))
        first = off == 0
        total = total_pages * size if (first or total_on_later) else 0
        data = {"total": total, "jobPostings": [mk(i, off=off) for i in range(n)]}
        if facets is not None:
            data["facets"] = facets
        return httpx.Response(200, json=data)

    return handler


US_FACETS = [
    {"facetParameter": "timeType", "values": [{"descriptor": "Full time", "id": "a"}]},
    {
        "facetParameter": "locationMainGroup",
        "values": [
            {
                "facetParameter": "locations",
                "values": [{"descriptor": "X", "id": "z"}],
            },
            {
                "facetParameter": "locationCountry",
                "descriptor": "Country",
                "values": [
                    {"descriptor": "Canada", "id": "c1"},
                    {"descriptor": "United States of America", "id": US_COUNTRY_ID},
                ],
            },
        ],
    },
]


async def test_fixture_pagination_cytokinetics():
    async with fixture_client("workday", "cytokinetics") as c:
        res = await A.fetch_jobs(s("cytokinetics/Cytokinetics", **CYTO), c)
    raw = [
        p
        for n in ("000", "001", "002")
        for p in fixture_json("workday", "cytokinetics", f"{n}.json")["jobPostings"]
    ]
    assert res.complete and len(res.jobs) == len(raw) == 60
    assert not res.warnings
    j = res.jobs[0]
    assert j.external_id == "R648"
    assert (
        j.url == "https://cytokinetics.wd1.myworkdayjobs.com/Cytokinetics" + raw[0]["externalPath"]
    )
    assert j.locations_raw == ["Radnor, Pennsylvania"]
    assert j.employment_type_raw == "Full time"
    assert j.posted_at is not None
    assert not j.needs_detail  # "Director, Regulatory Affairs" does not match the regex
    by_title = {x.title: x for x in res.jobs}
    assert by_title["Associate Director, Commercial Data Science"].needs_detail
    assert by_title["Senior Manager, Biostatistics"].needs_detail
    # "2 Locations" falls back to the path slug
    assert by_title["Cardiovascular Account Specialist - New Haven, CT"].locations_raw == ["Remote"]
    # 30+ days: unbounded
    old = [x for x in res.jobs if x.payload["postedOn"] == "Posted 30+ Days Ago"]
    assert old and all(x.posted_at is None for x in old)


async def test_fixture_us_facet_ias():
    async with fixture_client("workday", "integralads") as c:
        res = await A.fetch_jobs(s("integralads/IAScareers", **IAS), c)
    assert res.complete and len(res.jobs) == 9  # US-filtered view, not the 18 unfiltered


async def test_us_facet_disabled_lists_everything():
    # fixture only has offset 0; 18 < 20 so a single unfiltered page suffices
    async with fixture_client("workday", "integralads") as c:
        res = await A.fetch_jobs(s("integralads/IAScareers", us_facet=False, **IAS), c)
    assert res.complete and len(res.jobs) == 18


async def test_detail_fixture_mapping():
    async with fixture_client("workday", "cytokinetics") as c:
        res = await A.fetch_jobs(s("cytokinetics/Cytokinetics", **CYTO), c)
        job = next(j for j in res.jobs if j.title == "Associate Director, Commercial Data Science")
        assert job.needs_detail
        out = await A.fetch_detail(s("cytokinetics/Cytokinetics", **CYTO), job, c)
    info = fixture_json("workday", "cytokinetics", "004.json")["jobPostingInfo"]
    assert not out.needs_detail
    assert out.external_id == "R703"
    assert out.url == out.apply_url == info["externalUrl"]
    assert out.description_html == info["jobDescription"] and "<" in out.description_html
    assert out.locations_raw == ["Radnor, Pennsylvania", "South San Francisco, California"]
    assert out.employment_type_raw == "Full time"
    assert out.posted_at == datetime(2026, 11, 5, tzinfo=UTC)
    assert out.payload["detail"]["jobReqId"] == "R703"
    assert out.payload["title"] == job.title
    assert json.loads(json.dumps(out.payload)) == out.payload


async def test_detail_remote_type():
    async with fixture_client("workday", "integralads") as c:
        res = await A.fetch_jobs(s("integralads/IAScareers", **IAS), c)
        job = next(j for j in res.jobs if j.title == "Staff Machine Learning Engineer")
        out = await A.fetch_detail(s("integralads/IAScareers", **IAS), job, c)
    assert out.workplace_raw == "Hybrid"
    assert out.external_id == "R-101555"
    assert len(out.locations_raw) == 3


def test_map_round_trip():
    job = A._build(
        mk(1, "Data Scientist"), "h.wd5.myworkdayjobs.com", "Site", re.compile("data", re.I)
    )
    assert job and job.needs_detail
    again = A._map(job.payload)
    assert again == job
    detailed = A._map({**job.payload, "detail": {"jobReqId": "R9", "startDate": "2026-01-02"}})
    assert detailed and not detailed.needs_detail and detailed.external_id == "R9"
    assert A._map({"title": "x"}) is None and A._map("bad") is None


async def test_pagination_total_only_on_first_page():
    log = []
    async with mock_client(server(3, total_on_later=False, log=log)) as c:
        res = await A.fetch_jobs(s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s"), c)
    assert res.complete and len(res.jobs) == 60
    assert [b["offset"] for b in log] == [0, 20, 40]  # stops at offset >= total, no extra page
    assert all(b["limit"] == 20 and b["searchText"] == "" for b in log)


async def test_unknown_total_stops_on_empty_page():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        off = json.loads(request.content)["offset"]
        jobs = [mk(i, off=off) for i in range(20)] if off < 40 else []
        return httpx.Response(200, json={"total": 0, "jobPostings": jobs})

    async with mock_client(handler) as c:
        res = await A.fetch_jobs(s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s"), c)
    assert len(res.jobs) == 40 and res.complete


async def test_facet_applied_on_all_pages():
    log = []
    cfg = dict(host="h.wd5.myworkdayjobs.com", tenant="t", site="s")

    async with mock_client(server(2, facets=US_FACETS, log=log)) as c:
        res = await A.fetch_jobs(s("t/s", **cfg), c)
    assert len(res.jobs) == 40 and res.complete
    assert log[0]["appliedFacets"] == {}  # discovery page
    assert [b["offset"] for b in log] == [0, 0, 20]
    assert all(b["appliedFacets"] == {"locationCountry": [US_COUNTRY_ID]} for b in log[1:])


async def test_facet_switch_off():
    log = []
    async with mock_client(server(1, facets=US_FACETS, log=log)) as c:
        await A.fetch_jobs(
            s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s", us_facet=False), c
        )
    assert len(log) == 1 and log[0]["appliedFacets"] == {}


def test_find_us_facet():
    assert find_us_facet(US_FACETS) == ("locationCountry", US_COUNTRY_ID)
    named = [{"facetParameter": "Country", "values": [{"descriptor": "United States", "id": "q"}]}]
    assert find_us_facet(named) == ("Country", "q")
    assert (
        find_us_facet(
            [{"facetParameter": "Country", "values": [{"descriptor": "France", "id": "f"}]}]
        )
        is None
    )
    assert find_us_facet(None) is None


async def test_detail_only_for_title_regex():
    recs = [
        mk(1, "Data Scientist"),
        mk(2, "Receptionist"),
        mk(3, "Field Sales Rep"),
        mk(4, "ML Lead"),
    ]
    body = {"total": 4, "jobPostings": recs}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, json=body if json.loads(request.content)["offset"] == 0 else {"jobPostings": []}
        )

    async with mock_client(handler) as c:
        res = await A.fetch_jobs(s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s"), c)
    assert [j.needs_detail for j in res.jobs] == [True, False, True, True]
    async with mock_client(handler) as c:
        res = await A.fetch_jobs(
            s(
                "t/s",
                host="h.wd5.myworkdayjobs.com",
                tenant="t",
                site="s",
                detail_title_regex="receptionist",
            ),
            c,
        )
    assert [j.needs_detail for j in res.jobs] == [False, True, False, False]
    assert re.compile(DEFAULT_DETAIL_TITLE_REGEX, re.I).search("Senior HEOR Manager")


TODAY = date(2026, 11, 11)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Posted Today", date(2026, 11, 11)),
        ("Posted Yesterday", date(2026, 11, 10)),
        ("Posted 2 Days Ago", date(2026, 11, 9)),
        ("Posted 6 Days Ago", date(2026, 11, 5)),
        ("Posted 1 Day Ago", date(2026, 11, 10)),
        ("Posted 11 Days Ago", date(2026, 10, 31)),
        ("Posted 29 Days Ago", date(2026, 10, 13)),
        ("posted today", date(2026, 11, 11)),
        ("Posted 30+ Days Ago", None),
        ("Posted 100+ Days Ago", None),
        ("", None),
        (None, None),
        ("Veröffentlicht heute", None),
        (12, None),
    ],
)
def test_parse_relative(text, expected):
    got = parse_relative(text, TODAY)
    if expected is None:
        assert got is None
    else:
        assert got == datetime(expected.year, expected.month, expected.day, tzinfo=UTC)


async def test_empty_tenant():
    async with mock_client(server(0)) as c:
        res = await A.fetch_jobs(s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s"), c)
    assert res.jobs == [] and res.complete


async def test_404_raises():
    async with fixture_client("workday", "nosuchsite") as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(
                s(
                    "cytokinetics/NoSuchSite",
                    host=CYTO["host"],
                    tenant="cytokinetics",
                    site="NoSuchSite",
                ),
                c,
            )


async def test_malformed_skipped():
    body = {
        "total": 3,
        "jobPostings": [mk(1), {"title": "no path"}, {"externalPath": "/job/x/y_R1"}, "junk"],
    }

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, json=body if json.loads(request.content)["offset"] == 0 else {"jobPostings": []}
        )

    async with mock_client(handler) as c:
        res = await A.fetch_jobs(s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s"), c)
    assert len(res.jobs) == 1
    assert sum("skipped" in w for w in res.warnings) == 3


async def test_cap_hit_incomplete():
    async with mock_client(server(10)) as c:
        res = await A.fetch_jobs(
            s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s", max_pages=3), c
        )
    assert not res.complete and len(res.jobs) == 60 and any("cap" in w for w in res.warnings)
    async with mock_client(server(10)) as c:
        res = await A.fetch_jobs(
            s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s", max_jobs=30), c
        )
    assert not res.complete and len(res.jobs) == 40


async def test_short_listing_incomplete():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        off = json.loads(request.content)["offset"]
        return httpx.Response(200, json={"total": 500, "jobPostings": [mk(1)] if off == 0 else []})

    async with mock_client(handler) as c:
        res = await A.fetch_jobs(s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s"), c)
    assert not res.complete and res.warnings


async def test_missing_config():
    async with mock_client(server(1)) as c:
        with pytest.raises(ValueError):
            await A.fetch_jobs(s("t/s"), c)


def test_raw_job_type():
    j = A._map({"title": "T", "externalPath": "/job/US---NY/T_R-5", "_host": "h", "_site": "s"})
    assert isinstance(j, RawJob) and j.external_id == "R-5" and j.locations_raw == ["US - NY"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://cytokinetics.wd1.myworkdayjobs.com/Cytokinetics",
            ("cytokinetics/Cytokinetics", CYTO),
        ),
        (
            "https://phreesia.wd1.myworkdayjobs.com/en-US/Phreesia/",
            (
                "phreesia/Phreesia",
                {
                    "host": "phreesia.wd1.myworkdayjobs.com",
                    "tenant": "phreesia",
                    "site": "Phreesia",
                },
            ),
        ),
        (
            "https://trinitylifesciences.wd108.myworkdayjobs.com/Trinity/job/NY/Analyst_R1",
            (
                "trinitylifesciences/Trinity",
                {
                    "host": "trinitylifesciences.wd108.myworkdayjobs.com",
                    "tenant": "trinitylifesciences",
                    "site": "Trinity",
                },
            ),
        ),
        (
            "https://x.wd12.myworkdayjobs.com/wday/cxs/x/Ext/jobs",
            ("x/Ext", {"host": "x.wd12.myworkdayjobs.com", "tenant": "x", "site": "Ext"}),
        ),
        (
            "https://evolent.wd1.myworkdayjobs.com/External?q=clinical",
            (
                "evolent/External",
                {"host": "evolent.wd1.myworkdayjobs.com", "tenant": "evolent", "site": "External"},
            ),
        ),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://cytokinetics.wd1.myworkdayjobs.com/",
        "https://cytokinetics.wd1.myworkdayjobs.com/en-US/",
        "https://myworkdayjobs.com/foo",
        "https://wd5.myworkdayjobs.com/",
        "https://cytokinetics.com/careers",
        "https://boards.greenhouse.io/x",
        "https://wd12.myworkdaysite.com/recruiting/x/Y",
        "not a url",
    ],
)
def test_match_url_none(url):
    assert A.match_url(url) is None
