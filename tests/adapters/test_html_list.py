import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from daily_job_tool.adapters.html_list import ConfigError, HtmlListAdapter
from daily_job_tool.http.errors import NotFound
from tests.adapters._helpers import FIXTURES, fixture_client, mock_client, src

A = HtmlListAdapter()


def site_cfg(site: str) -> dict:
    return json.loads((FIXTURES / "html_list" / site / "manifest.json").read_text())["config"]


def routes(table: dict[str, tuple[int, str]]):
    """MockTransport handler keyed by 'path?query' (or path) -> (status, body)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        status, body = table.get(key, table.get(request.url.path, (404, "")))
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    return handler


def listing(*hrefs: str) -> str:
    return "<ul>" + "".join(f'<li><a class="j" href="{h}">x</a></li>' for h in hrefs) + "</ul>"


BASE = {"list_url": "https://x.test/careers", "job_link_selector": "a.j"}


# ---------------------------------------------------------------- recorded sites
@pytest.mark.parametrize(
    ("site", "n_jobs", "dated"), [("hyro", 7, True), ("medidata", 10, True), ("doceree", 24, False)]
)
async def test_site_fixtures_end_to_end(site, n_jobs, dated):
    cfg = site_cfg(site)
    s = src("html_list", site, **cfg)
    async with fixture_client("html_list", site) as c:
        res = await A.fetch_jobs(s, c)
        assert res.complete and len(res.jobs) == n_jobs and not res.warnings
        assert len({j.external_id for j in res.jobs}) == n_jobs
        assert all(j.needs_detail and j.url.startswith("https://") for j in res.jobs)
        manifest = json.loads((FIXTURES / "html_list" / site / "manifest.json").read_text())
        recorded = {e["url"] for e in manifest["exchanges"]}
        detailed = [j for j in res.jobs if j.url in recorded]
        assert len(detailed) >= 2
        for j in detailed:
            d = await A.fetch_detail(s, j, c)
            assert not d.needs_detail and d.title and d.locations_raw
            assert d.department_raw and d.description_html and len(d.description_html) > 200
            assert json.loads(json.dumps(d.payload)) == d.payload
            if dated:
                assert d.posted_at is not None and d.posted_at.tzinfo is not None
                assert (
                    datetime(2025, 1, 1, tzinfo=UTC)
                    < d.posted_at
                    < datetime(2027, 1, 1, tzinfo=UTC)
                )
            else:
                assert d.posted_at is None
            # stored detail payload rebuilds the same job without HTTP
            r = A._map(d.payload)
            assert r is not None and r.title == d.title and r.posted_at == d.posted_at


async def test_hyro_values():
    s = src("html_list", "hyro", **site_cfg("hyro"))
    async with fixture_client("html_list", "hyro") as c:
        res = await A.fetch_jobs(s, c)
        d = await A.fetch_detail(s, res.jobs[0], c)
    assert res.jobs[0].external_id == "careers/17.D60"
    assert d.title == "Engagement Manager (NYC)"
    assert d.locations_raw == ["New York City, US"]
    assert d.department_raw == "Delivery" and d.employment_type_raw == "Full-time"
    assert d.posted_at == datetime(2026, 9, 2, 14, 3, 12, tzinfo=UTC)


async def test_medidata_sitemap_lastmod_not_used_and_label_selectors():
    s = src("html_list", "medidata", **site_cfg("medidata"))
    async with fixture_client("html_list", "medidata") as c:
        res = await A.fetch_jobs(s, c)
        j = next(x for x in res.jobs if x.external_id == "senior-data-engineer-549311")
        d = await A.fetch_detail(s, j, c)
    assert j.payload == {"url": j.url}  # no lastmod in the (hash-stable) list payload
    assert d.locations_raw == ["United States - NJ, Iselin"]
    assert d.department_raw == "Information Technology"
    assert d.posted_at == datetime(2026, 10, 19, 21, 52, 38, tzinfo=UTC)


async def test_doceree_values():
    s = src("html_list", "doceree", **site_cfg("doceree"))
    async with fixture_client("html_list", "doceree") as c:
        res = await A.fetch_jobs(s, c)
        j = next(x for x in res.jobs if x.external_id.endswith("director-corporate-communications"))
        d = await A.fetch_detail(s, j, c)
    assert j.url == "https://www.doceree.com/careers/director-corporate-communications"
    assert d.title == "Director - Corporate Communications"
    assert d.locations_raw == ["Short Hills, NJ"] and d.employment_type_raw == "Full-time"
    assert d.department_raw == "Marketing" and d.posted_at is None


# ---------------------------------------------------------------- synthetic
JOBPOSTING = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"WebPage","datePublished":"2020-01-01"},
{"@type":"JobPosting","title":"Data &amp; ML Engineer","datePosted":"2026-10-01",
 "employmentType":["FULL_TIME"],"description":"&lt;p&gt;Build things&lt;/p&gt;",
 "jobLocationType":"TELECOMMUTE",
 "jobLocation":[{"@type":"Place","address":{"addressLocality":"Austin","addressRegion":"TX",
   "addressCountry":{"name":"US"}}}],
 "baseSalary":{"currency":"USD","value":{"minValue":100000,"maxValue":150000,"unitText":"YEAR"}}}]}
</script></head><body><h1>ignored</h1></body></html>"""


async def test_jsonld_jobposting_preferred():
    cfg = {**BASE, "detail": {"title": "h1", "posted_at": {"selector": "yoast"}}}
    table = {"/careers": (200, listing("/jobs/1")), "/jobs/1": (200, JOBPOSTING)}
    async with mock_client(routes(table)) as c:
        s = src("html_list", "t", **cfg)
        res = await A.fetch_jobs(s, c)
        d = await A.fetch_detail(s, res.jobs[0], c)
    assert d.title == "Data & ML Engineer"
    assert d.posted_at == datetime(2026, 10, 1, tzinfo=UTC)  # datePosted beats Yoast 2020
    assert d.locations_raw == ["Austin, TX, US"] and d.workplace_raw == "remote"
    assert d.employment_type_raw == "full time"
    assert d.description_html == "<p>Build things</p>"
    assert (d.salary.min, d.salary.max, d.salary.currency, d.salary.period) == (
        100000.0, 150000.0, "USD", "year",
    )  # fmt: skip


async def test_selector_date_sources_and_untrusted_dates_are_none():
    page = (
        '<html><head><meta property="article:published_time" content="2026-05-06T07:08:09+02:00">'
        "</head><body><h1> T </h1><time datetime='2026-03-04'>x</time></body></html>"
    )
    table = {"/careers": (200, listing("/jobs/1")), "/jobs/1": (200, page)}
    expect = {
        "meta:article:published_time": datetime(2026, 5, 6, 5, 8, 9, tzinfo=UTC),
        "time": datetime(2026, 3, 4, tzinfo=UTC),
        "yoast": None,  # no schema graph on the page
        "jsonld": None,
    }
    async with mock_client(routes(table)) as c:
        for sel, want in expect.items():
            cfg = {**BASE, "detail": {"posted_at": {"selector": sel}}}
            s = src("html_list", "t", **cfg)
            res = await A.fetch_jobs(s, c)
            d = await A.fetch_detail(s, res.jobs[0], c)
            assert d.title == "T"
            assert d.posted_at == want, sel
        # no posted_at configured at all
        s = src("html_list", "t", **BASE)
        d = await A.fetch_detail(s, (await A.fetch_jobs(s, c)).jobs[0], c)
        assert d.posted_at is None


SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://x.test/jobs/a-1/</loc><lastmod>2026-11-08</lastmod></url>
<url><loc>https://x.test/jobs/b-2/</loc><lastmod>2026-11-08</lastmod></url>
<url><loc>https://x.test/jobs/b-2/</loc></url>
<url><loc>https://x.test/about/</loc></url>
</urlset>"""


async def test_sitemap_mode():
    cfg = {
        "list_url": "https://x.test/sitemap.xml",
        "mode": "sitemap",
        "link_filter_regex": "/jobs/",
        "id_from": "/jobs/([a-z]-\\d+)",
    }
    page = "<h1>Role</h1><div class=d>desc</div>"
    table = {"/sitemap.xml": (200, SITEMAP), "/jobs/a-1/": (200, page)}
    async with mock_client(routes(table)) as c:
        s = src("html_list", "t", **cfg)
        res = await A.fetch_jobs(s, c)
        d = await A.fetch_detail(s, res.jobs[0], c)
    assert [j.external_id for j in res.jobs] == ["a-1", "b-2"] and res.complete
    assert d.title == "Role" and d.posted_at is None


async def test_sitemap_index_and_bad_xml():
    cfg = {"list_url": "https://x.test/sitemap.xml", "mode": "sitemap"}
    idx = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></sitemapindex>'
    for body, exc in ((idx, ConfigError), ("<html>nope", ValueError)):
        async with mock_client(routes({"/sitemap.xml": (200, body)})) as c:
            with pytest.raises(exc):
                await A.fetch_jobs(src("html_list", "t", **cfg), c)


async def test_id_from_query_and_relative_links_deduped():
    cfg = {**BASE, "id_from": "query:id", "base_url": "https://jobs.x.test"}
    html = listing("/view?id=7", "/view?id=7#top", "/view?id=8", "/view", "mailto:a@b.c")
    async with mock_client(routes({"/careers": (200, html)})) as c:
        res = await A.fetch_jobs(src("html_list", "t", **cfg), c)
    assert [j.external_id for j in res.jobs] == ["7", "8"]
    assert res.jobs[0].url == "https://jobs.x.test/view?id=7"
    assert any("no id derivable" in w for w in res.warnings) and not res.complete


async def test_pagination_cap_marks_incomplete():
    cfg = {**BASE, "pagination": {"param": "page", "max_pages": 3}}
    table = {
        "/careers": (200, listing("/j/1")),
        "/careers?page=2": (200, listing("/j/2")),
        "/careers?page=3": (200, listing("/j/3")),
        "/careers?page=4": (200, listing("/j/4")),
    }
    async with mock_client(routes(table)) as c:
        res = await A.fetch_jobs(src("html_list", "t", **cfg), c)
    assert [j.external_id for j in res.jobs] == ["j/1", "j/2", "j/3"]
    assert not res.complete and any("max_pages" in w for w in res.warnings)


async def test_pagination_ends_on_404_or_repeat_and_is_complete():
    cfg = {**BASE, "pagination": {"param": "page"}}
    table = {"/careers": (200, listing("/j/1")), "/careers?page=2": (200, listing("/j/2"))}
    async with mock_client(routes(table)) as c:  # page 3 is 404
        res = await A.fetch_jobs(src("html_list", "t", **cfg), c)
    assert len(res.jobs) == 2 and res.complete
    table["/careers?page=3"] = (200, listing("/j/2"))  # repeats: no new links
    async with mock_client(routes(table)) as c:
        res = await A.fetch_jobs(src("html_list", "t", **cfg), c)
    assert len(res.jobs) == 2 and res.complete


async def test_max_jobs_cap():
    cfg = {**BASE, "max_jobs": 2}
    async with mock_client(routes({"/careers": (200, listing("/j/1", "/j/2", "/j/3"))})) as c:
        res = await A.fetch_jobs(src("html_list", "t", **cfg), c)
    assert len(res.jobs) == 2 and not res.complete and any("max_jobs" in w for w in res.warnings)


async def test_empty_list_is_incomplete_with_warning():
    async with mock_client(routes({"/careers": (200, "<html><body>nothing</body></html>")})) as c:
        res = await A.fetch_jobs(src("html_list", "t", **BASE), c)
    assert res.jobs == [] and not res.complete and res.warnings


async def test_list_404_raises():
    async with mock_client(routes({})) as c:
        with pytest.raises(NotFound):
            await A.fetch_jobs(src("html_list", "t", **BASE), c)


async def test_detail_404_isolated_and_missing_title_raises():
    table = {
        "/careers": (200, listing("/j/1", "/j/2", "/j/3")),
        "/j/1": (200, "<h1>One</h1>"),
        "/j/3": (200, "<p>no heading</p>"),
    }
    async with mock_client(routes(table)) as c:
        s = src("html_list", "t", **BASE)
        res = await A.fetch_jobs(s, c)
        assert res.complete and len(res.jobs) == 3
        ok = await A.fetch_detail(s, res.jobs[0], c)
        assert ok.title == "One"
        with pytest.raises(NotFound):  # ingest records this as a skipped job with a warning
            await A.fetch_detail(s, res.jobs[1], c)
        with pytest.raises(ValueError, match="no title"):
            await A.fetch_detail(s, res.jobs[2], c)


@pytest.mark.parametrize(
    ("cfg", "msg"),
    [
        ({}, "empty config"),
        ({"list_url": "careers"}, "list_url"),
        ({"list_url": "https://x.test/", "mode": "xml"}, "mode"),
        ({"list_url": "https://x.test/"}, "job_link_selector is required"),
        ({"list_url": "https://x.test/", "job_link_selector": "a >> !!"}, "invalid CSS"),
        ({**BASE, "link_filter_regex": "("}, "link_filter_regex"),
        ({**BASE, "pagination": {"max_pages": 3}}, "pagination"),
        ({**BASE, "pagination": {"param": "p", "max_pages": 0}}, "max_pages"),
        ({**BASE, "max_jobs": 0}, "max_jobs"),
        ({**BASE, "id_from": "/jobs/\\d+"}, "capture group"),
        ({**BASE, "id_from": "query:"}, "parameter name"),
        ({**BASE, "detail": {"salary": "p"}}, "unknown detail"),
        ({**BASE, "detail": {"title": "!!"}}, "detail.title"),
        ({**BASE, "detail": {"title": 5}}, "detail.title"),
        ({**BASE, "bogus": 1}, "unknown config key"),
        (
            {"list_url": "https://x.test/s.xml", "mode": "sitemap", "pagination": {"param": "p"}},
            "sitemap",
        ),
    ],
)
async def test_config_validation(cfg, msg):
    async with mock_client(routes({})) as c:
        with pytest.raises(ConfigError, match=msg):
            await A.fetch_jobs(src("html_list", "tok", **cfg), c)


def test_registered_and_metadata():
    assert (
        A.name == "html_list" and A.tier == 3 and A.hosts and A.match_url("https://x.test") is None
    )
    assert Path(FIXTURES / "html_list").is_dir()
