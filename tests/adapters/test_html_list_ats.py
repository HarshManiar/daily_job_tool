"""html_list configs for Phenom (sitemap), Jobvite and applytojob/JazzHR boards (offline)."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from daily_job_tool.adapters.html_list import HtmlListAdapter
from tests.adapters._helpers import FIXTURES, fixture_client, mock_client, src

A = HtmlListAdapter()

# site -> (jobs in the trimmed list fixture, expect datePosted)
SITES = {
    "zelis": (10, True),
    "chewy": (10, True),
    "icf": (10, True),
    "crowe": (10, True),
    "yelp": (10, True),
    "sutrobio": (4, True),
    "ziffdavis": (7, False),  # Jobvite pages without JSON-LD: no trustworthy date
    "krystalbiotech": (7, True),
    "shipwell": (3, True),
}


def manifest(site: str) -> dict:
    return json.loads((FIXTURES / "html_list" / site / "manifest.json").read_text())


@pytest.mark.parametrize("site", list(SITES))
async def test_ats_fixtures_end_to_end(site):
    n_jobs, dated = SITES[site]
    m = manifest(site)
    s = src("html_list", site, **m["config"])
    async with fixture_client("html_list", site) as c:
        res = await A.fetch_jobs(s, c)
        assert res.complete and len(res.jobs) == n_jobs and not res.warnings
        ids = [j.external_id for j in res.jobs]
        assert len(set(ids)) == n_jobs
        assert all("/" not in i and " " not in i for i in ids)
        recorded = {e["url"] for e in m["exchanges"]}
        detailed = [j for j in res.jobs if j.url in recorded]
        assert len(detailed) >= 2
        for j in detailed:
            d = await A.fetch_detail(s, j, c)
            assert not d.needs_detail and d.title and d.title != j.title.lower()
            assert d.description_html and len(d.description_html) > 200
            assert d.external_id == j.external_id
            if dated:
                assert d.posted_at is not None and d.posted_at.tzinfo is not None
                assert (
                    datetime(2025, 1, 1, tzinfo=UTC)
                    < d.posted_at
                    < datetime(2027, 1, 1, tzinfo=UTC)
                )
            else:
                assert d.posted_at is None
            assert json.loads(json.dumps(d.payload)) == d.payload
            r = A._map(d.payload)
            assert r is not None and r.title == d.title and r.posted_at == d.posted_at
        # locations are mapped for most postings (a few boards leave some blank)
        with_loc = [d for d in [await A.fetch_detail(s, j, c) for j in detailed] if d.locations_raw]
        assert with_loc


@pytest.mark.parametrize(
    ("site", "first_id"),
    [
        ("zelis", "ZOBZDHUSJR111812EXTERNALENUS"),
        ("chewy", "R29198"),
        ("yelp", "13979"),
        ("sutrobio", "oBq0Afwl"),
        ("krystalbiotech", "knU7AEd5Op"),
    ],
)
async def test_ids_derived_from_url(site, first_id):
    s = src("html_list", site, **manifest(site)["config"])
    async with fixture_client("html_list", site) as c:
        res = await A.fetch_jobs(s, c)
    assert first_id in {j.external_id for j in res.jobs}


async def test_phenom_sitemap_filter_only_job_urls():
    cfg = manifest("zelis")["config"]
    base = "https://careers.zelis.com"
    locs = [
        f"{base}/us/en",
        f"{base}/us/en/search-results",
        f"{base}/us/en/job/R1/Data-Engineer",
        f"{base}/us/en/job/R2/Analyst",
        f"{base}/us/en/job/R1/Data-Engineer",  # duplicate
        f"{base}/us/en/c/healthcare-jobs",
        f"{base}/us/en/job/R3",  # no slug: not a job page
    ]
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{u}</loc></url>" for u in locs)
        + "</urlset>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=xml, headers={"content-type": "application/xml"})

    res = await A.fetch_jobs(src("html_list", "zelis", **cfg), mock_client(handler))
    assert [j.external_id for j in res.jobs] == ["R1", "R2"]
    assert res.complete


async def test_applytojob_list_ignores_nav_links():
    cfg = manifest("krystalbiotech")["config"]
    page = (
        '<ul><li class="list-group-item"><a href="/apply/AbC123/Chemist">Chemist</a></li>'
        '<li class="list-group-item"><a href="/apply">All jobs</a></li>'
        '<li class="list-group-item"><a href="https://other.test/x">x</a></li></ul>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    res = await A.fetch_jobs(src("html_list", "krystalbiotech", **cfg), mock_client(handler))
    assert [j.external_id for j in res.jobs] == ["AbC123"]
