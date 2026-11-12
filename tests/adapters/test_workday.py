import json
import re
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

import daily_job_tool.adapters.workday as W
from daily_job_tool.adapters.base import RawJob
from daily_job_tool.adapters.html_list import ConfigError
from daily_job_tool.adapters.workday import (
    DEFAULT_DETAIL_TITLE_REGEX,
    US_COUNTRY_ID,
    WorkdayAdapter,
    find_us_facet,
    parse_relative,
)
from daily_job_tool.http.errors import NotFound
from daily_job_tool.pipeline.ingest import payload_hash
from tests.adapters._helpers import fixture_client, fixture_json, mock_client, src

A = WorkdayAdapter()
CYTO = dict(host="cytokinetics.wd1.myworkdayjobs.com", tenant="cytokinetics", site="Cytokinetics")
IAS = dict(host="integralads.wd1.myworkdayjobs.com", tenant="integralads", site="IAScareers")


TODAY = date(2026, 11, 11)  # America/Los_Angeles date of the fixture recording (00:25Z on 11-12)


@pytest.fixture(autouse=True)
def _anchor(monkeypatch):
    monkeypatch.setattr(W, "la_today", lambda: TODAY)


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
    assert j.posted_at == parse_relative(raw[0]["postedOn"], TODAY)
    assert j.posted_at is not None and j.payload["_posted"] == j.posted_at.date().isoformat()
    assert "postedOn" not in j.payload  # volatile relative text is not stored
    assert not j.needs_detail  # "Director, Regulatory Affairs" does not match the regex
    by_title = {x.title: x for x in res.jobs}
    assert by_title["Associate Director, Commercial Data Science"].needs_detail
    assert by_title["Senior Manager, Biostatistics"].needs_detail
    # "2 Locations" falls back to the path slug
    assert by_title["Cardiovascular Account Specialist - New Haven, CT"].locations_raw == ["Remote"]
    # 30+ days: unbounded
    old = {r["externalPath"] for r in raw if r["postedOn"] == "Posted 30+ Days Ago"}
    assert old
    for x in res.jobs:
        if x.payload["externalPath"] in old:
            assert x.posted_at is None and "_posted" not in x.payload


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
        mk(1, "Data Scientist"), "h.wd5.myworkdayjobs.com", "Site", re.compile("data", re.I), TODAY
    )
    assert job and job.needs_detail
    again = A._map(job.payload)
    assert again == job
    detailed = A._map({**job.payload, "detail": {"jobReqId": "R9", "startDate": "2026-01-02"}})
    assert (
        detailed and not detailed.needs_detail and detailed.external_id == "R1"
    )  # id never from detail
    assert A._map({"title": "x"}) is None and A._map("bad") is None


async def test_pagination_total_only_on_first_page():
    log = []
    async with mock_client(server(3, total_on_later=False, log=log)) as c:
        res = await A.fetch_jobs(s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s"), c)
    assert res.complete and len(res.jobs) == 60
    assert [b["offset"] for b in log] == [0, 20, 40, 60]  # pages until an empty page
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
    assert [b["offset"] for b in log] == [0, 0, 20, 40]
    assert all(b["appliedFacets"] == {"locationCountry": [US_COUNTRY_ID]} for b in log[1:])


async def test_facet_switch_off():
    log = []
    async with mock_client(server(1, facets=US_FACETS, log=log)) as c:
        await A.fetch_jobs(
            s("t/s", host="h.wd5.myworkdayjobs.com", tenant="t", site="s", us_facet=False), c
        )
    assert [b["offset"] for b in log] == [0, 20]  # no discovery restart
    assert all(b["appliedFacets"] == {} for b in log)


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
    assert j.posted_at is None


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


# ---------------------------------------------------------------- review-fix regressions

CFG = dict(host="h.wd5.myworkdayjobs.com", tenant="t", site="s")


def listing(pages, log=None):
    """Handler serving pages = fn(offset) -> dict body."""

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = json.loads(request.content)
        if log is not None:
            log.append(body)
        return httpx.Response(200, json=pages(body))

    return handler


async def run(handler, **cfg):
    async with mock_client(handler) as c:
        return await A.fetch_jobs(s("t/s", **CFG, **cfg), c)


async def test_overlapping_pages_not_complete():
    # unstable sort: page at offset 20 overlaps the previous page by 5 -> 5 jobs never seen
    n = 100

    def pages(b):
        off = b["offset"]
        start = off - 5 if off == 20 else off
        return {
            "total": n,
            "jobPostings": [mk(i) for i in range(max(start, 0), start + 20) if i < n],
        }

    res = await run(listing(pages))
    assert len(res.jobs) == 95 and not res.complete and res.warnings


async def test_jobs_added_mid_crawl_total_grows():
    # 3 jobs posted at the head after page 0 shift everything; the total grows to 103
    state = {"jobs": list(range(100))}

    def pages(b):
        off = b["offset"]
        if off == 20:
            state["jobs"] = [1000, 1001, 1002, *state["jobs"]]
        js = state["jobs"]
        return {"total": len(js), "jobPostings": [mk(i) for i in js[off : off + 20]]}

    res = await run(listing(pages))
    ids = {j.external_id for j in res.jobs}
    assert ids >= {f"R{i}" for i in range(100)}  # every pre-existing job still reached
    assert not res.complete  # the 3 new head jobs were not seen: 100 < max total 103


async def test_frozen_total_still_reaches_oldest():
    # total only reported on page 0 (stale, 100); old code stopped at offset 100 and never
    # reached the 3 oldest jobs, which a later close would have wrongly dropped
    state = {"jobs": list(range(100))}

    def pages(b):
        off = b["offset"]
        if off == 20:
            state["jobs"] = [1000, 1001, 1002, *state["jobs"]]
        js = state["jobs"]
        return {"total": 100 if off == 0 else 0, "jobPostings": [mk(i) for i in js[off : off + 20]]}

    res = await run(listing(pages))
    assert {j.external_id for j in res.jobs} >= {f"R{i}" for i in range(100)}


async def test_offset_ignored_stops_after_one_repeat():
    log = []

    def pages(b):
        return {"total": 0, "jobPostings": [mk(i) for i in range(20)]}

    res = await run(listing(pages, log))
    assert len(log) == 2 and len(res.jobs) == 20 and not res.complete
    assert any("no new" in w for w in res.warnings)


async def test_exact_full_list_complete_no_slack():
    res = await run(
        listing(
            lambda b: {
                "total": 100,
                "jobPostings": [mk(i) for i in range(b["offset"], min(b["offset"] + 20, 100))],
            }
        )
    )
    assert res.complete and len(res.jobs) == 100 and not res.warnings
    # one short of the reported total is NOT complete (old code allowed 90%)
    res = await run(
        listing(
            lambda b: {
                "total": 100,
                "jobPostings": [mk(i) for i in range(b["offset"], min(b["offset"] + 20, 99))],
            }
        )
    )
    assert len(res.jobs) == 99 and not res.complete


async def test_malformed_counted_in_accounting():
    def pages(b):
        if b["offset"]:
            return {"jobPostings": []}
        return {"total": 3, "jobPostings": [mk(1), mk(2), {"title": "no path"}]}

    res = await run(listing(pages))
    assert res.complete and len(res.jobs) == 2
    assert sum("skipped" in w for w in res.warnings) == 1


async def test_cap_warning_text():
    res = await run(
        listing(
            lambda b: {"total": 1000, "jobPostings": [mk(i, off=b["offset"]) for i in range(20)]}
        ),
        max_pages=2,
    )
    assert not res.complete
    assert any(
        "tenant too large for full enumeration; jobs will not auto-close" in w for w in res.warnings
    )


@pytest.mark.parametrize("relative", ["Posted Today", "Posted Yesterday", "Posted 3 Days Ago"])
def test_posted_absolute_and_relative_text_not_stored(relative):
    rec = {**mk(1), "postedOn": relative}
    job = A._build(rec, "h", "s", re.compile("zzz"), TODAY)
    assert "postedOn" not in job.payload
    days = {"Posted Today": 0, "Posted Yesterday": 1, "Posted 3 Days Ago": 3}[relative]
    assert job.posted_at == datetime.combine(TODAY - timedelta(days=days), datetime.min.time(), UTC)


def test_payload_hash_stable_across_days():
    rx = re.compile("analyst", re.I)
    rels = ["Posted Today", "Posted Yesterday", "Posted 2 Days Ago", "Posted 3 Days Ago"]
    jobs = [
        A._build({**mk(1), "postedOn": r}, "h", "s", rx, TODAY + timedelta(days=i))
        for i, r in enumerate(rels)
    ]
    assert all(j.payload == jobs[0].payload for j in jobs)
    assert len({payload_hash(j.payload) for j in jobs}) == 1
    assert len({j.posted_at for j in jobs}) == 1


async def test_hash_stable_end_to_end(monkeypatch):
    def pages(rel):
        return lambda b: {
            "total": 2,
            "jobPostings": [
                {**mk(i), "postedOn": rel} for i in range(b["offset"], min(b["offset"] + 20, 2))
            ],
        }

    monkeypatch.setattr(W, "la_today", lambda: TODAY)
    d1 = await run(listing(pages("Posted Today")))
    monkeypatch.setattr(W, "la_today", lambda: TODAY + timedelta(days=1))
    d2 = await run(listing(pages("Posted Yesterday")))
    assert [payload_hash(j.payload) for j in d1.jobs] == [payload_hash(j.payload) for j in d2.jobs]


def test_30plus_has_no_date_key():
    job = A._build({**mk(1), "postedOn": "Posted 30+ Days Ago"}, "h", "s", re.compile("x"), TODAY)
    assert job.posted_at is None and "_posted" not in job.payload and "postedOn" not in job.payload


def test_la_anchor_is_los_angeles_date(monkeypatch):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 12, 0, 25, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(W, "datetime", FakeDT)
    assert W.la_today() == date(2026, 11, 11)  # UTC date would be 11-12


def test_map_does_not_reparse_relative_against_now():
    stored = {
        "title": "x",
        "externalPath": "/job/a/x_R1",
        "_host": "h",
        "_site": "s",
        "_posted": "2026-11-08",
    }
    assert A._map(stored).posted_at == datetime(2026, 11, 8, tzinfo=UTC)
    stale = {**stored, "postedOn": "Posted Today"}  # legacy payloads: ignored, not re-parsed
    assert A._map({k: v for k, v in stale.items() if k != "_posted"}).posted_at is None


# external ids


def test_external_id_from_path_only_and_distinct():
    rx = re.compile("data", re.I)
    base = {"title": "Data Analyst", "bulletFields": ["R9"]}
    a = A._build({**base, "externalPath": "/job/NY/x_R9"}, "h", "s", rx, TODAY)
    b = A._build({**base, "externalPath": "/job/Chicago-IL/x_R9-1"}, "h", "s", rx, TODAY)
    assert (a.external_id, b.external_id) == ("R9", "R9-1")
    rec = {"title": "Data Analyst", "externalPath": "/job/NY/Senior-Analyst_R-101600"}
    assert A._build(rec, "h", "s", rx, TODAY).external_id == "R-101600"
    noid = A._build({**rec, "externalPath": "/job/NY/plainslug"}, "h", "s", rx, TODAY)
    assert noid.external_id == "plainslug"


def test_colliding_bullets_do_not_collide():
    rx = re.compile("data", re.I)
    jobs = [
        A._build(
            {
                "title": f"Data {i}",
                "externalPath": f"/job/NY/x_R{i}",
                "bulletFields": ["Full time", f"R{i}"],
            },
            "h",
            "s",
            rx,
            TODAY,
        )
        for i in range(3)
    ]
    assert [j.external_id for j in jobs] == ["R0", "R1", "R2"]


def test_external_id_unchanged_by_detail():
    rx = re.compile("data", re.I)
    lj = A._build(
        {
            "title": "Data Analyst",
            "externalPath": "/job/NY/Data-Analyst_R587-1",
            "bulletFields": ["R587-1"],
        },
        "h",
        "s",
        rx,
        TODAY,
    )
    dj = A._map(
        {**lj.payload, "detail": {"jobReqId": "R587", "location": "NY", "startDate": "2026-11-11"}}
    )
    assert lj.external_id == dj.external_id == "R587-1"


async def test_detail_fetch_keeps_id_and_drops_posted_on():
    async with fixture_client("workday", "cytokinetics") as c:
        res = await A.fetch_jobs(s("cytokinetics/Cytokinetics", **CYTO), c)
        job = next(j for j in res.jobs if j.title == "Associate Director, Commercial Data Science")
        out = await A.fetch_detail(s("cytokinetics/Cytokinetics", **CYTO), job, c)
    assert out.external_id == job.external_id == "R703"
    assert "postedOn" not in out.payload["detail"]


# regex

TITLES_MATCH = [
    t.strip()
    for t in """Data Analyst II|Senior Data Engineer|Manager, Commercial Analytics|Director, Real World Evidence|Associate Director, RWE
HEOR Manager|Manager, Health Economics & Outcomes Research|Health Economics Manager|Director, Market Access Strategy|Value & Access Lead
Claims Data Analyst|Payment Integrity Analyst|Medical Economics Manager|Healthcare Economics Consultant|Consumer Insights Manager
Business Intelligence Developer|Power BI Developer|Tableau Developer|Sr. Specialist, Forecasting|Demand Planner
Pricing & Contracting Manager|Gross-to-Net Manager|Sales Operations Manager|Sales Ops Manager|Revenue Operations Analyst
RevOps Manager|Incentive Compensation Manager|Associate Consultant|Engagement Manager|Senior Associate, Life Sciences Strategy
Epidemiologist|Director, Epidemiology|SAS Programmer|Actuarial Analyst|Actuary
Market Research Manager|Clinical Informatics Specialist|Population Health Manager|Strategy & Operations Manager|Quantitative Researcher
Evidence Generation Lead|Patient Services Analytics Lead|Brand Strategy Manager, Oncology|Performance Measurement Manager|Risk Adjustment Coding Specialist""".replace(
        "\n", "|"
    ).split("|")
]
TITLES_NO = [
    "Registered Nurse",
    "Forklift Operator",
    "Barista",
    "Administrative Assistant",
    "Truck Driver",
    "Receptionist",
    "Phlebotomist",
]


def test_default_regex_matches_all_realistic_titles():
    rx = re.compile(DEFAULT_DETAIL_TITLE_REGEX, re.I)
    assert len(TITLES_MATCH) == 45
    assert [t for t in TITLES_MATCH if not rx.search(t)] == []
    assert [t for t in TITLES_NO if rx.search(t)] == []


# locations


def test_n_locations_slug_comma_separated():
    def loc(path):
        return A._map(
            {
                "title": "T",
                "locationsText": "2 Locations",
                "externalPath": path,
                "_host": "h",
                "_site": "s",
            }
        ).locations_raw

    assert loc("/job/Radnor-Pennsylvania/T_R1") == ["Radnor, Pennsylvania"]
    assert loc("/job/South-San-Francisco-California/T_R1") == ["South San Francisco, California"]
    assert loc("/job/New-York-NY/T_R1") == ["New York, NY"]
    assert loc("/job/Remote/T_R1") == ["Remote"]


# facet / errors


async def test_facet_4xx_falls_back_unfiltered():
    log = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = json.loads(request.content)
        log.append(body)
        if body["appliedFacets"]:
            return httpx.Response(400, json={"error": "bad facet"})
        off = body["offset"]
        return httpx.Response(
            200,
            json={
                "total": 25,
                "facets": US_FACETS,
                "jobPostings": [mk(i, off=off) for i in range(max(0, min(20, 25 - off)))],
            },
        )

    res = await run(handler)
    assert len(res.jobs) == 25 and res.complete
    assert any("retrying unfiltered" in w for w in res.warnings)
    assert [bool(b["appliedFacets"]) for b in log] == [False, True, False, False]


async def test_us_id_discovered_from_descriptor():
    facets = [
        {"facetParameter": "Country", "values": [{"descriptor": "United States", "id": "zzz"}]}
    ]
    log = []
    await run(
        listing(
            lambda b: {
                "total": 1,
                "facets": facets,
                "jobPostings": [mk(1)] if b["offset"] == 0 else [],
            },
            log,
        )
    )
    assert log[1]["appliedFacets"] == {"Country": ["zzz"]}
    # a value with the well-known id but no US descriptor is not trusted
    odd = [{"facetParameter": "Country", "values": [{"descriptor": "Narnia", "id": US_COUNTRY_ID}]}]
    assert find_us_facet(odd) is None


@pytest.mark.parametrize("status", [400, 422])
async def test_first_page_4xx_is_not_found(status):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(status, json={"error": "x"})

    with pytest.raises(NotFound):
        await run(handler)


@pytest.mark.parametrize(
    "host", ["acme.impl-wd5.myworkdayjobs.com", "acme.wd5-impl.myworkdayjobs.com"]
)
async def test_impl_hosts_rejected(host):
    assert A.match_url(f"https://{host}/Site") is None
    async with mock_client(server(1)) as c:
        with pytest.raises(ConfigError, match="sandbox"):
            await A.fetch_jobs(s("t/s", host=host, tenant="t", site="s"), c)


def _chunks(n, total=None, repeat_tail=False, facets=None):
    """Handler serving n distinct jobs in 20-item pages; past the end it repeats the tail
    (like Workday) or returns an empty page."""

    def pages(b):
        off = b["offset"]
        if off >= n:
            items = [mk(i) for i in range(max(0, n - 18), n)] if repeat_tail else []
        else:
            items = [mk(i) for i in range(off, min(off + 20, n))]
        body = {"total": n if total is None else total, "jobPostings": items}
        if facets is not None:
            body["facets"] = facets
        return body

    return pages


async def test_short_list_no_trailing_request():
    log = []
    res = await run(listing(_chunks(18, repeat_tail=True), log))
    assert res.complete and len(res.jobs) == 18 and not res.warnings
    assert len(log) == 1


async def test_three_pages_total_45():
    log = []
    res = await run(listing(_chunks(45, repeat_tail=True), log))
    assert res.complete and len(res.jobs) == 45 and not res.warnings
    assert [b["offset"] for b in log] == [0, 20, 40]


async def test_tail_repeat_after_seen_ge_total_is_complete():
    log = []
    res = await run(listing(_chunks(40, repeat_tail=True), log))  # full last page
    assert res.complete and len(res.jobs) == 40 and not res.warnings
    assert [b["offset"] for b in log] == [0, 20, 40]


async def test_offset_ignored_total_100_incomplete():
    log = []
    res = await run(
        listing(lambda b: {"total": 100, "jobPostings": [mk(i) for i in range(20)]}, log)
    )
    assert len(log) == 2 and len(res.jobs) == 20 and not res.complete
    assert any("no new" in w for w in res.warnings)


async def test_total_grows_before_coverage_incomplete():
    # page 0 reports 20 of 25; page 1 (short, 5 items) reports total 27 -> 2 never seen
    def pages(b):
        off = b["offset"]
        if off == 0:
            return {"total": 25, "jobPostings": [mk(i) for i in range(20)]}
        return (
            {"total": 27, "jobPostings": [mk(i) for i in range(20, 25)]}
            if off == 20
            else {
                "total": 27,
                "jobPostings": [],
            }
        )

    res = await run(listing(pages))
    assert len(res.jobs) == 25 and not res.complete


async def test_total_only_on_first_page_short_list():
    def pages(b):
        if b["offset"] == 0:
            return {"total": 18, "jobPostings": [mk(i) for i in range(18)]}
        return {"total": 0, "jobPostings": [mk(i) for i in range(18)]}

    log = []
    res = await run(listing(pages, log))
    assert res.complete and len(res.jobs) == 18 and len(log) == 1


async def test_us_facet_short_list_no_trailing_request():
    log = []
    res = await run(listing(_chunks(18, repeat_tail=True, facets=US_FACETS), log))
    assert res.complete and len(res.jobs) == 18
    assert [b["offset"] for b in log] == [0, 0]  # discovery + filtered page 1 only
