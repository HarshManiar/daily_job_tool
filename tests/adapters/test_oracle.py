import httpx
import pytest

from daily_job_tool.adapters.html_list import ConfigError
from daily_job_tool.adapters.oracle import OracleAdapter, find_us_location_id
from daily_job_tool.http.errors import NotFound
from daily_job_tool.pipeline.ingest import payload_hash
from tests.adapters._helpers import fixture_client, mock_client, src

A = OracleAdapter()
HOST, SITE = "ekgn.fa.us6.oraclecloud.com", "CX_2001"
MYRIAD = dict(host=HOST, site=SITE, page_size=10)
FWD = dict(host="eguq.fa.us2.oraclecloud.com", site="MovingYouForward", page_size=50)


def s(token="ekgn/CX_2001", **cfg):
    return src("oracle", token, **cfg)


def rec(i, title="Analyst", **kw):
    return {
        "Id": str(i),
        "Title": f"{title} {i}",
        "PostedDate": "2026-11-10",
        "PrimaryLocation": "Austin, TX, United States",
        "WorkplaceType": "Remote",
        "Distance": 1.0,
        "Relevancy": 9,
        "secondaryLocations": [],
        **kw,
    }


def wrap(recs, total, us=True):
    w = {"TotalJobsCount": total, "requisitionList": recs, "locationsFacet": []}
    if us:
        w["locationsFacet"] = [{"Id": 77, "Name": "United States", "TotalCount": total}]
    return {"items": [w], "count": 1, "hasMore": False}


def server(all_recs, *, total=None, ignore_offset=False, log=None, us=True, grow=None):
    """Mock list endpoint over `all_recs`; `grow(n_requests)` may append recs mid-crawl."""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        q = str(request.url)
        if "recruitingCEJobRequisitionDetails" in q:
            return httpx.Response(200, json={"items": []})
        fin = q.split("finder=")[1]
        kv = dict(p.split("=", 1) for p in fin.split(";", 1)[1].split(","))
        off, lim = int(kv["offset"]), int(kv["limit"])
        if log is not None:
            log.append(kv)
        state["n"] += 1
        if grow:
            grow(state["n"], all_recs)
        if ignore_offset:
            off = 0
        t = len(all_recs) if total is None else total
        return httpx.Response(200, json=wrap(all_recs[off : off + lim], t, us))

    return handler


async def run(handler, **cfg):
    async with mock_client(handler) as c:
        return await A.fetch_jobs(s(**{"host": HOST, "site": SITE, **cfg}), c)


async def test_fixture_pagination_myriad():
    async with fixture_client("oracle", "myriad") as c:
        res = await A.fetch_jobs(s(**MYRIAD), c)
    assert res.complete and len(res.jobs) == 24 and not res.warnings
    j = next(x for x in res.jobs if x.external_id == "6352")
    assert j.title == "MRD Sales Consultant - Dallas, Texas"
    assert j.url == f"https://{HOST}/hcmUI/CandidateExperience/en/sites/{SITE}/job/6352"
    assert j.apply_url == j.url
    assert j.posted_at.date().isoformat() == "2026-11-10"
    assert j.workplace_raw == "Remote" and j.locations_raw
    assert "Distance" not in j.payload and "Relevancy" not in j.payload
    assert any(x.needs_detail for x in res.jobs)


async def test_fixture_forward_air_complete():
    async with fixture_client("oracle", "forwardair") as c:
        res = await A.fetch_jobs(s("eguq/MovingYouForward", **FWD), c)
    assert res.complete and len(res.jobs) == 83
    assert all(j.posted_at for j in res.jobs)
    assert len({j.external_id for j in res.jobs}) == 83


async def test_fixture_detail_and_missing():
    async with fixture_client("oracle", "myriad") as c:
        res = await A.fetch_jobs(s(**MYRIAD), c)
        by = {j.external_id: j for j in res.jobs}
        d = await A.fetch_detail(s(**MYRIAD), by["6352"], c)
        assert d.external_id == "6352" and not d.needs_detail
        assert d.description_html and "<" in d.description_html
        assert d.employment_type_raw == "Full time"
        assert d.department_raw == "Commercial Sales"
        assert d.salary and d.salary.min and d.salary.min > 1000
        assert d.posted_at.hour or d.posted_at.minute or d.posted_at.second
        assert A._map(d.payload).model_dump() == d.model_dump()
        with pytest.raises(NotFound):
            await A.fetch_detail(
                s(**MYRIAD), by["6352"].model_copy(update={"external_id": "999999999"}), c
            )


async def test_first_page_400_is_not_found():
    async with fixture_client("oracle", "nosuchhost") as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(s(host=HOST, site=SITE, us_facet=False, page_size=1), c)


async def test_us_facet_applied_and_discovered():
    log: list = []
    recs = [rec(i) for i in range(5)]
    res = await run(server(recs, log=log))
    assert res.complete and len(res.jobs) == 5
    assert log[0]["limit"] == "1" and "selectedLocationsFacet" not in log[0]
    assert all(k.get("selectedLocationsFacet") == "77" for k in log[1:])


async def test_no_us_facet_lists_unfiltered():
    log: list = []
    res = await run(server([rec(i) for i in range(3)], log=log, us=False))
    assert res.complete and len(res.jobs) == 3
    assert all("selectedLocationsFacet" not in k for k in log)


async def test_us_facet_disabled():
    log: list = []
    await run(server([rec(1)], log=log), us_facet=False)
    assert len(log) == 1 and "selectedLocationsFacet" not in log[0]


def test_find_us_location_id():
    w = {
        "locationsFacet": [
            {"Id": 1, "Name": "TX, United States"},
            {"Id": 2, "Name": "United States"},
        ]
    }
    assert find_us_location_id(w) == 2
    assert find_us_location_id({"locationsFacet": []}) is None


async def test_short_last_page_no_trailing_request():
    log: list = []
    res = await run(server([rec(i) for i in range(25)], log=log), page_size=10, us_facet=False)
    assert res.complete and len(res.jobs) == 25
    assert [k["offset"] for k in log] == ["0", "10", "20"]


async def test_exact_multiple_ends_on_empty_page():
    log: list = []
    res = await run(server([rec(i) for i in range(20)], log=log), page_size=10, us_facet=False)
    assert res.complete and len(res.jobs) == 20
    # a full final page costs one more request, which comes back empty
    assert [k["offset"] for k in log] == ["0", "10", "20"]


async def test_ignores_offset_server_incomplete():
    res = await run(
        server([rec(i) for i in range(30)], ignore_offset=True), page_size=10, us_facet=False
    )
    assert not res.complete and len(res.jobs) == 10
    assert any("no new" in w for w in res.warnings)


async def test_overlapping_pages_incomplete_when_short():
    recs = [rec(i) for i in range(10)]
    calls = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=wrap(recs, 25, False))
        return httpx.Response(200, json=wrap(recs, 25, False))  # same page again

    res = await run(handler, page_size=10, us_facet=False)
    assert not res.complete and len(res.jobs) == 10


async def test_tail_repeat_after_all_seen_is_complete():
    recs = [rec(i) for i in range(10)]
    calls = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(200, json=wrap(recs, 10, False))

    res = await run(handler, page_size=10, us_facet=False)
    assert res.complete and len(res.jobs) == 10 and calls["n"] == 2


async def test_midcrawl_growth_incomplete():
    recs = [rec(i) for i in range(20)]

    def grow(n, allr):
        if n == 1:  # total reported on page 1 is 20; later pages report a larger total
            return

    calls = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        off = (calls["n"] - 1) * 10
        total = 20 if calls["n"] == 1 else 25  # 5 jobs appeared that we never list
        return httpx.Response(200, json=wrap(recs[off : off + 10], total, False))

    res = await run(handler, page_size=10, us_facet=False)
    assert not res.complete and len(res.jobs) == 20
    assert any("of reported total 25" in w for w in res.warnings)


async def test_total_larger_than_listed_incomplete():
    res = await run(server([rec(i) for i in range(5)], total=9), us_facet=False)
    assert not res.complete


async def test_malformed_records_counted_not_lost():
    recs = [rec(1), {"Id": "2"}, rec(3)]
    res = await run(server(recs), us_facet=False)
    assert res.complete and len(res.jobs) == 2
    assert any("malformed" in w for w in res.warnings)


async def test_cap_marks_incomplete():
    res = await run(server([rec(i) for i in range(50)]), page_size=10, max_jobs=20, us_facet=False)
    assert not res.complete and len(res.jobs) == 20
    assert any("too large for full enumeration" in w for w in res.warnings)
    res = await run(server([rec(i) for i in range(50)]), page_size=10, max_pages=2, us_facet=False)
    assert not res.complete
    assert any("too large" in w for w in res.warnings)


async def test_filtered_first_page_error_retries_unfiltered():
    recs = [rec(i) for i in range(3)]
    base = server(recs)

    def handler(request):
        if "selectedLocationsFacet" in str(request.url):
            return httpx.Response(400, json={})
        return base(request)

    res = await run(handler)
    assert res.complete and len(res.jobs) == 3
    assert any("US facet request failed" in w for w in res.warnings)


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_4xx_first_page_not_found(status):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(status, json={})

    with pytest.raises(NotFound):
        await run(handler, us_facet=False)
    with pytest.raises(NotFound):
        await run(handler)


async def test_gating_and_stable_payload():
    recs = [rec(1, "Senior Data Scientist"), rec(2, "Warehouse Associate")]
    r1 = await run(server(recs), us_facet=False)
    by = {j.title: j for j in r1.jobs}
    assert (
        by["Senior Data Scientist 1"].needs_detail and not by["Warehouse Associate 2"].needs_detail
    )
    r2 = await run(server(recs), us_facet=False, detail_title_regex="warehouse")
    assert [j.needs_detail for j in r2.jobs] == [False, True]
    # volatile fields change between "days"; stored payload hash must not
    day2 = [{**r, "Distance": 99.0, "Relevancy": 1, "TrendingFlag": True} for r in recs]
    r3 = await run(server(day2), us_facet=False)
    assert [payload_hash(j.payload) for j in r1.jobs] == [payload_hash(j.payload) for j in r3.jobs]


async def test_id_stability_and_detail_does_not_change_id():
    async with fixture_client("oracle", "myriad") as c:
        r1 = await A.fetch_jobs(s(**MYRIAD), c)
        r2 = await A.fetch_jobs(s(**MYRIAD), c)
        j = next(x for x in r1.jobs if x.external_id == "6352")
        d = await A.fetch_detail(s(**MYRIAD), j, c)
    assert [x.external_id for x in r1.jobs] == [x.external_id for x in r2.jobs]
    assert d.external_id == j.external_id and d.url == j.url


def test_map_round_trip_and_locations():
    p = rec(5, secondaryLocations=[{"Name": "Boston, MA, United States"}])
    p.update({"_host": HOST, "_site": SITE, "_nd": False, "JobSchedule": "Part time"})
    j = A._map(p)
    assert j.locations_raw == ["Austin, TX, United States", "Boston, MA, United States"]
    assert j.employment_type_raw == "Part time"
    assert A._map(j.payload).model_dump() == j.model_dump()
    assert A._map({"Id": "1"}) is None and A._map("x") is None


@pytest.mark.parametrize(
    "cfg",
    [
        {},
        {"host": HOST},
        {"site": SITE},
        {"host": "example.com", "site": SITE},
        {"host": "ekgn-test.fa.us6.oraclecloud.com", "site": SITE},
        {"host": "fa-abcd-saasfadev1.fa.ocs.oraclecloud.com", "site": SITE},
        {"host": HOST, "site": "bad site/x"},
    ],
)
async def test_config_errors(cfg):
    async with mock_client(lambda r: httpx.Response(200)) as c:
        with pytest.raises(ConfigError):
            await A.fetch_jobs(s(**cfg), c)


def test_config_accepts_perficient_prod_host():
    assert A._cfg(s(host="fa-etqd-saasfaprod1.fa.ocs.oraclecloud.com", site="CX_1"))


def test_match_url():
    u = "https://eeih.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_6/job/1028"
    assert A.match_url(u) == ("eeih/CX_6", {"host": "eeih.fa.us2.oraclecloud.com", "site": "CX_6"})
    assert (
        A.match_url(
            "https://iaziqy.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/UberCareers/"
        )[0]
        == "iaziqy/UberCareers"
    )
    assert A.match_url("https://example.com/hcmUI/CandidateExperience/en/sites/CX_6") is None
    assert A.match_url("https://eeih.fa.us2.oraclecloud.com/other") is None
    assert A.match_url("not a url") is None
