import re
from datetime import UTC, datetime

import httpx
import pytest

from daily_job_tool.adapters.html_list import ConfigError
from daily_job_tool.adapters.icims import IcimsAdapter, _format_location
from daily_job_tool.adapters.workday import DEFAULT_DETAIL_TITLE_REGEX
from daily_job_tool.http.errors import Blocked, NotFound
from daily_job_tool.pipeline.ingest import payload_hash
from tests.adapters._helpers import FIXTURES, fixture_client, mock_client, src

A = IcimsAdapter()
ROCKET = "careers-rocketpharma"
NAVITUS = "careers-navitus"


def s(token, **cfg):
    return src("icims", token, host=f"{token}.icims.com", **cfg)


def row(i, title="Analyst", href=None, loc="US-NJ-Cranbury"):
    href = href or f"https://x.icims.com/jobs/{i}/job-{i}/job?in_iframe=1"
    return (
        f'<div class="row"><div class="col-xs-6 header left"><span class="sr-only field-label">'
        f'Location</span><span>\n{loc}</span></div><div class="col-xs-6 header right">'
        f'<span class="sr-only">ID</span><span>2026-{i}</span></div><div class="col-xs-12 title">'
        f'<a href="{href}" class="iCIMS_Anchor"><h3>\n{title} {i}</h3></a></div></div>'
    )


def page(rows, of=None, name="Acme"):
    paging = (
        f'<div class="iCIMS_Paging"><div class="iCIMS_PagingBatch"><a class="selected">'
        f'<span class="sr-only">Page</span>1<span class="sr-only">of {of} , Current Page</span>'
        f"</a></div></div>"
        if of
        else ""
    )
    return (
        f"<html><head><title>Job Listings at {name}</title></head><body>"
        f'<div class="container-fluid iCIMS_JobsTable">{"".join(rows)}</div>{paging}</body></html>'
    )


def server(pages, log=None):
    """pages: list of html strings for pr=0,1,...; past the end -> an empty (error) page."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if log is not None:
            log.append(str(request.url))
        n = int(request.url.params.get("pr", "0"))
        html = pages[n] if n < len(pages) else "<html><body><div class=iCIMS_ErrorMsg>x</div>"
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    return handler


async def test_fixture_single_page_rocket():
    res = await A.fetch_jobs(s(ROCKET), fixture_client("icims", ROCKET))
    assert res.complete and res.warnings == []
    assert res.board_name == "Rocket Pharmaceuticals"
    assert len(res.jobs) == 9
    j = next(j for j in res.jobs if j.external_id == "2161")
    assert j.title == "Senior Clinical Manufacturing Associate"
    assert j.url == (
        "https://careers-rocketpharma.icims.com/jobs/2161/"
        "senior-clinical-manufacturing-associate/job"
    )
    assert j.locations_raw == ["Cranbury, NJ"]
    assert j.employment_type_raw == "Regular Full-Time"
    assert j.department_raw == "Manufacturing"
    assert j.posted_at is None  # list level never invents a date
    assert not j.needs_detail  # title not in the relevance gate
    assert "description" not in str(j.payload.keys())


async def test_fixture_two_pages_navitus_pager_total():
    log = []
    transport = fixture_client("icims", NAVITUS)
    res = await A.fetch_jobs(s(NAVITUS), transport)
    assert res.complete and len(res.jobs) == 85  # 50 + 35
    assert res.board_name == "Navitus Health Solutions, LLC"
    j = res.jobs[0]
    assert j.workplace_raw == "remote"
    assert (j.salary.min, j.salary.max, j.salary.currency, j.salary.period) == (
        142128.0,
        177660.0,
        "USD",
        "year",
    )
    assert j.locations_raw == []  # "US-" carries no place
    del log


async def test_stops_after_pager_last_page_without_extra_request():
    log = []
    pages = [page([row(i) for i in range(3)], of=2), page([row(i) for i in range(3, 5)], of=2)]
    res = await A.fetch_jobs(s("x"), mock_client(server(pages, log)))
    assert res.complete and len(res.jobs) == 5
    assert [u.rsplit("pr=", 1)[1] for u in log] == ["0", "1"]


async def test_no_pager_ends_on_empty_page():
    log = []
    pages = [page([row(1), row(2)]), page([row(3)])]
    res = await A.fetch_jobs(s("x"), mock_client(server(pages, log)))
    assert res.complete and len(res.jobs) == 3
    assert len(log) == 3  # third request is the empty past-the-end page


async def test_no_new_links_stops_incomplete():
    pages = [page([row(1), row(2)], of=5), page([row(1), row(2)], of=5)]
    res = await A.fetch_jobs(s("x"), mock_client(server(pages)))
    assert not res.complete and len(res.jobs) == 2
    assert any("no new jobs" in w for w in res.warnings)


async def test_empty_page_before_pager_end_is_incomplete():
    pages = [page([row(1)], of=3)]
    res = await A.fetch_jobs(s("x"), mock_client(server(pages)))
    assert not res.complete
    assert any("pager reports 3" in w for w in res.warnings)


async def test_empty_first_page_is_not_trusted():
    res = await A.fetch_jobs(s("x"), mock_client(server([page([], of=1)])))
    assert res.jobs == [] and not res.complete and res.warnings


async def test_page_cap_incomplete():
    pages = [page([row(i + 10 * n) for i in range(2)], of=50) for n in range(5)]
    res = await A.fetch_jobs(s("x", max_pages=2), mock_client(server(pages)))
    assert not res.complete and len(res.jobs) == 4
    assert any("will not auto-close" in w for w in res.warnings)


async def test_job_cap_incomplete():
    pages = [page([row(i + 10 * n) for i in range(2)], of=50) for n in range(5)]
    res = await A.fetch_jobs(s("x", max_jobs=3), mock_client(server(pages)))
    assert not res.complete and len(res.jobs) == 4


async def test_malformed_rows_skipped_and_dupes_collapsed():
    bad = row(9, href="https://x.icims.com/somewhere/else")
    res = await A.fetch_jobs(s("x"), mock_client(server([page([row(1), row(1), bad], of=1)])))
    assert [j.external_id for j in res.jobs] == ["1"]
    assert res.complete and "skipped malformed row" in res.warnings


async def test_blocked_and_not_found_propagate():
    def h403(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text="denied")

    with pytest.raises(Blocked):
        await A.fetch_jobs(s("x"), mock_client(h403))
    with pytest.raises(NotFound):
        await A.fetch_jobs(s("x"), mock_client(lambda r: httpx.Response(404)))


async def test_gate_by_title_regex():
    pages = [page([row(1, "Senior Data Scientist"), row(2, "Facilities Technician")], of=1)]
    res = await A.fetch_jobs(s("x"), mock_client(server(pages)))
    assert [j.needs_detail for j in res.jobs] == [True, False]
    res = await A.fetch_jobs(s("x", detail_title_regex="technician"), mock_client(server(pages)))
    assert [j.needs_detail for j in res.jobs] == [False, True]
    assert re.search(DEFAULT_DETAIL_TITLE_REGEX, "Data Scientist", re.I)


async def test_detail_jsonld_mapping():
    c = fixture_client("icims", ROCKET)
    res = await A.fetch_jobs(s(ROCKET), c)
    job = next(j for j in res.jobs if j.external_id == "2143")
    assert job.needs_detail and job.posted_at is None
    d = await A.fetch_detail(s(ROCKET), job, c)
    assert d.external_id == "2143" and d.url == job.url and not d.needs_detail
    assert d.posted_at == datetime(2026, 7, 16, 4, 0, tzinfo=UTC)
    assert d.locations_raw == ["Cranbury, NJ"]
    assert d.employment_type_raw == "FULL_TIME"
    assert d.salary.min == 236000 and d.salary.max == 354000 and d.salary.currency == "USD"
    assert d.description_html and "<h2>" in d.description_html
    assert d.department_raw


async def test_detail_remote_jsonld_navitus():
    c = fixture_client("icims", NAVITUS)
    res = await A.fetch_jobs(s(NAVITUS), c)
    job = next(j for j in res.jobs if j.external_id == "7011")
    d = await A.fetch_detail(s(NAVITUS), job, c)
    assert d.workplace_raw == "remote" and d.locations_raw == ["Remote, US"]
    assert d.posted_at == datetime(2026, 11, 10, 5, 0, tzinfo=UTC)


async def test_detail_html_fallback_without_jsonld():
    c = fixture_client("icims", ROCKET)
    res = await A.fetch_jobs(s(ROCKET), c)
    job = next(j for j in res.jobs if j.external_id == "2143")
    html = (FIXTURES / "icims" / ROCKET / "004.html").read_text()
    html = re.sub(r'<script type="application/ld\+json".*?</script>', "", html, flags=re.S)
    assert "ld+json" not in html

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    d = await A.fetch_detail(s(ROCKET), job, mock_client(handler))
    assert d.posted_at is None  # only JSON-LD datePosted is trusted
    assert d.description_html and "<h2>" in d.description_html
    assert d.locations_raw == ["Cranbury, NJ"]
    assert d.external_id == "2143"


async def test_detail_404_propagates():
    c = fixture_client("icims", ROCKET)
    res = await A.fetch_jobs(s(ROCKET), c)
    job = res.jobs[0].model_copy(
        update={"payload": {**res.jobs[0].payload, "path": "/jobs/9999999/nosuch/job"}}
    )
    with pytest.raises(NotFound):
        await A.fetch_detail(s(ROCKET), job, c)


async def test_map_round_trip_and_stable_payload():
    c = fixture_client("icims", ROCKET)
    first = await A.fetch_jobs(s(ROCKET), c)
    second = await A.fetch_jobs(s(ROCKET), fixture_client("icims", ROCKET))  # "next day"
    assert [payload_hash(j.payload) for j in first.jobs] == [
        payload_hash(j.payload) for j in second.jobs
    ]
    job = next(j for j in first.jobs if j.external_id == "2143")
    d = await A.fetch_detail(s(ROCKET), job, c)
    rebuilt = IcimsAdapter._map(d.payload)
    assert rebuilt == d
    for j in first.jobs:
        assert IcimsAdapter._map(j.payload) == j
    assert IcimsAdapter._map({"title": "x"}) is None and IcimsAdapter._map(None) is None


def test_format_location():
    assert _format_location("US-NJ-Cranbury") == ["Cranbury, NJ"]
    assert _format_location("CA-QC-Montreal") == ["Montreal, QC, CA"]
    assert _format_location("US-") == []
    assert _format_location("US-NJ-Cranbury | US-MA-Boston") == ["Cranbury, NJ", "Boston, MA"]
    assert _format_location("Remote") == ["Remote"]


def test_config_validation():
    with pytest.raises(ConfigError):
        IcimsAdapter._host(src("icims", "x", host="evil.example.com"))
    with pytest.raises(ConfigError):
        IcimsAdapter._host(src("icims", "login"))
    assert IcimsAdapter._host(src("icims", "careers-zs")) == "careers-zs.icims.com"


def test_match_url():
    m = IcimsAdapter.match_url
    assert m("https://careers-navitus.icims.com/jobs/search?ss=1") == (
        "careers-navitus",
        {"host": "careers-navitus.icims.com"},
    )
    assert m("https://uscareers-medpace.icims.com/jobs/login?loginOnly=1")[0] == (
        "uscareers-medpace"
    )
    assert m("https://login.icims.com/u/login/identifier") is None
    assert m("https://cdn02.icims.com/a/x.css") is None
    assert m("https://www.icims.com/") is None
    assert m("https://icims.com/") is None
    assert m("https://example.com/") is None
    assert m("not a url") is None
