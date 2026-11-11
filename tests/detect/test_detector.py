import csv
import json
import re

import httpx
import pytest
import respx

from daily_job_tool.detect.detector import detect_company
from daily_job_tool.detect.runner import run_batch
from daily_job_tool.http.client import CrawlClient
from daily_job_tool.http.ratelimit import HostLimiter

UA = "DailyJobToolBot/0.1 (+https://example.invalid/bot)"
HTML = {"content-type": "text/html; charset=utf-8"}


def make() -> CrawlClient:
    async def nosleep(_s: float) -> None:
        return None

    limiter = HostLimiter(overrides=(), default_rate=1000, default_burst=1000, sleep=nosleep)
    return CrawlClient(UA, limiter=limiter, sleep=nosleep, retry_delays=())


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        m.get(url__regex=r".*/robots\.txt").respond(404)
        yield m


def page(mock, url, body="<html><body></body></html>", status=200, headers=None):
    return mock.get(url__regex=re.escape(url) + r"/?$").respond(
        status, text=body, headers=headers or HTML
    )


async def run(domain="acme.com"):
    async with make() as c:
        return await detect_company(c, domain)


async def test_iframe_greenhouse(mock):
    page(
        mock,
        "https://acme.com",
        '<iframe src="https://boards.greenhouse.io/embed/job_board?for=acmeco"></iframe>',
    )
    d = await run()
    assert (d.status, d.ats, d.token) == ("detected", "greenhouse", "acmeco")
    assert d.confidence >= 0.95


async def test_redirect_to_workday(mock):
    mock.get(url__regex=r"https://acme\.com/?$").respond(
        302, headers={"location": "https://acme.wd5.myworkdayjobs.com/en-US/Ext"}
    )
    page(mock, "https://acme.wd5.myworkdayjobs.com/en-US/Ext")
    d = await run()
    assert (d.status, d.ats, d.token) == ("detected", "workday", "acme")


async def test_script_ashby(mock):
    page(
        mock, "https://acme.com", '<script src="https://jobs.ashbyhq.com/acme/embed?v=1"></script>'
    )
    d = await run()
    assert (d.ats, d.token) == ("ashby", "acme")


async def test_link_icims(mock):
    page(mock, "https://acme.com", '<a href="https://careers-acme.icims.com/jobs">Careers</a>')
    d = await run()
    assert (d.ats, d.token, d.status) == ("icims", "acme", "detected")


JSONLD = '<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"JobPosting","title":"x"}]}</script>'


async def test_custom_page_jsonld(mock):
    page(mock, "https://acme.com", '<a href="/careers">Careers</a>')
    page(mock, "https://acme.com/careers", f"<html>{JSONLD}</html>")
    d = await run()
    assert d.status == "custom_page"
    assert d.jsonld_jobposting is True


async def test_custom_page_without(mock):
    page(mock, "https://acme.com", '<a href="/careers">Careers</a>')
    page(mock, "https://acme.com/careers", "<html><h1>Join us</h1></html>")
    d = await run()
    assert d.status == "custom_page"
    assert d.jsonld_jobposting is False
    assert d.ats == "unknown"


async def test_robots_disallow(mock):
    mock.get(url__regex=r".*/robots\.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    d = await run()
    assert d.status == "blocked_by_robots"


async def test_bot_wall(mock):
    mock.get(url__regex=r"https://acme\.com/?$").respond(
        403, text="Attention Required! | Cloudflare", headers=HTML
    )
    d = await run()
    assert d.status == "bot_wall"
    assert d.fetches == 1  # stopped, no retry


async def test_careers_link_discovery(mock):
    page(mock, "https://acme.com", '<a href="https://jobs.acme-careers.org/home">Open roles</a>')
    page(
        mock,
        "https://jobs.acme-careers.org/home",
        '<iframe src="https://jobs.lever.co/acme"></iframe>',
    )
    d = await run()
    assert (d.ats, d.token) == ("lever", "acme")


async def test_fetch_cap(mock):
    mock.get(url__regex=r"https://.*").respond(404)
    calls = []

    def count(request):
        if not request.url.path.endswith("robots.txt"):
            calls.append(str(request.url))
        return httpx.Response(404)

    mock.get(url__regex=r"https://.*").mock(side_effect=count)
    mock.get(url__regex=r".*/robots\.txt").respond(404)
    d = await run()
    assert len(calls) <= 6
    assert d.status == "unreachable"


async def test_linkedin_only(mock):
    page(mock, "https://acme.com", '<a href="/careers">Careers</a>')
    page(
        mock,
        "https://acme.com/careers",
        '<a href="https://www.linkedin.com/company/acme/jobs">See jobs</a>',
    )
    d = await run()
    assert (d.status, d.ats) == ("linkedin_only", "linkedin-only")


async def test_no_careers_page(mock):
    page(mock, "https://acme.com", "<html>hello</html>")
    d = await run()
    assert d.status == "no_careers_page"


async def test_runner_resume(mock, tmp_path):
    page(mock, "https://acme.com", '<iframe src="https://jobs.lever.co/acme"></iframe>')
    src = tmp_path / "in.csv"
    src.write_text("name,domain,industry\nAcme,acme.com,software\nB,b.com,software\n")
    out = tmp_path / "out.csv"
    async with make() as c:
        await run_batch(src, out, limit=1, client=c)
        await run_batch(src, out, client=c)
    rows = list(csv.DictReader(out.open()))
    assert [r["domain"] for r in rows] == ["acme.com", "b.com"]
    assert rows[0]["ats"] == "lever"
    json.dumps(rows)
