"""Detector additions: CareerPuck, Rippling, Gem, Darwinbox, Comeet, Paycom, Workable /j/,
Greenhouse embed script, and following /careers/jobs, /open-roles, careers.<domain> links."""

import re

import pytest
import respx

from daily_job_tool.detect.detector import MAX_FETCHES
from tests.detect.test_detector import HTML, page, run


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        m.get(url__regex=r".*/robots\.txt").respond(404)
        yield m


def api(mock, url, body):
    return mock.get(url__regex=re.escape(url) + r"/?$").respond(200, json=body)


async def test_careerpuck_resolves_underlying_ats(mock):
    page(
        mock,
        "https://acme.com",
        '<a href="https://app.careerpuck.com/job-board/acme-co/job/abc123">Engineer</a>',
    )
    api(
        mock,
        "https://api.careerpuck.com/v1/public/job-boards/acme-co",
        {
            "jobs": [
                {
                    "atsSourcePlatform": "ashby",
                    "jobBoard": {"atsSourcePlatform": "ashby", "atsSourceId": "acme-board"},
                }  # fmt: skip
            ]
        },
    )
    d = await run()
    assert (d.status, d.ats, d.token) == ("detected", "ashby", "acme-board")
    assert "careerpuck" in d.evidence
    assert d.fetches <= MAX_FETCHES + 1


async def test_careerpuck_unresolvable_keeps_careerpuck(mock):
    page(mock, "https://acme.com", '<a href="https://app.careerpuck.com/job-board/zzz">Jobs</a>')
    mock.get(url__regex=r"https://api\.careerpuck\.com/.*").respond(404)
    d = await run()
    assert (d.status, d.ats, d.token) == ("detected", "careerpuck", "zzz")
    assert "careerpuck" in d.notes


async def test_careerpuck_falls_back_to_slug_and_top_level_platform(mock):
    page(mock, "https://acme.com", '<a href="https://app.careerpuck.com/job-board/slug1">x</a>')
    api(
        mock,
        "https://api.careerpuck.com/v1/public/job-boards/slug1",
        {"jobs": [{"atsSourcePlatform": "Greenhouse"}]},
    )
    d = await run()
    assert (d.ats, d.token) == ("greenhouse", "slug1")


async def test_rippling_embed_div_and_script(mock):
    page(
        mock,
        "https://acme.com",
        '<div id="rr-job-board" data-job-board-id="carbon-health"></div>'
        '<script src="https://static-assets.ripplingcdn.com/ats/embeds/job-board.v1.js"></script>',
    )
    d = await run()
    assert (d.status, d.ats, d.token) == ("detected", "rippling", "carbon-health")


async def test_rippling_board_link(mock):
    page(mock, "https://acme.com", '<a href="https://ats.rippling.com/acme-inc/jobs">Jobs</a>')
    d = await run()
    assert (d.ats, d.token) == ("rippling", "acme-inc")


async def test_gem_inline_fetch(mock):
    page(
        mock,
        "https://acme.com",
        "<script>fetch('https://api.gem.com/job_board/v0/mindbloom/job_posts/')"
        ".then(r => r.json())</script>",
    )
    d = await run()
    assert (d.ats, d.token) == ("gem", "mindbloom")


async def test_gem_board_link(mock):
    page(mock, "https://acme.com", '<a href="https://jobs.gem.com/acme">Careers</a>')
    d = await run()
    assert (d.ats, d.token) == ("gem", "acme")


async def test_darwinbox_link(mock):
    page(
        mock,
        "https://acme.com",
        '<a href="https://acme.darwinbox.com/ms/candidatev2/main/careers/allJobs">Openings</a>',
    )
    d = await run()
    assert (d.status, d.ats, d.token) == ("detected", "darwinbox", "acme")
    assert d.config == {"host": "acme.darwinbox.com"}


async def test_comeet_link(mock):
    page(mock, "https://acme.com", '<a href="https://www.comeet.co/jobs/acme/12.345">Jobs</a>')
    d = await run()
    assert (d.ats, d.token) == ("comeet", "acme")


async def test_paycom_link(mock):
    page(
        mock,
        "https://acme.com",
        '<a href="https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey=ABC123">Jobs</a>',
    )
    d = await run()
    assert (d.ats, d.token) == ("paycom", "ABC123")


async def test_greenhouse_embed_script_protocol_relative(mock):
    page(
        mock,
        "https://acme.com",
        '<div id="grnhse_app"></div><script>var s=document.createElement("script");'
        's.src="//boards.greenhouse.io/embed/job_board/js?for=acmeaffiliate";</script>',
    )
    d = await run()
    assert (d.ats, d.token) == ("greenhouse", "acmeaffiliate")


async def test_greenhouse_embed_script_tag(mock):
    page(
        mock,
        "https://acme.com",
        '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script>',
    )
    d = await run()
    assert (d.ats, d.token) == ("greenhouse", "acmeco")


async def test_workable_shortcode_follows_careers_jobs_and_reads_canonical(mock):
    page(mock, "https://acme.com", '<a href="/careers">Careers</a>')
    page(mock, "https://acme.com/careers", '<h1>Careers</h1><a href="/careers/jobs">See jobs</a>')
    page(
        mock,
        "https://acme.com/careers/jobs",
        '<a href="https://apply.workable.com/j/8EE4D3922D">Engineer</a>',
    )
    page(
        mock,
        "https://apply.workable.com/j/8EE4D3922D",
        '<link rel="canonical" href="https://apply.workable.com/acme-analytics/j/8EE4D3922D/">',
    )
    d = await run()
    assert (d.status, d.ats, d.token) == ("detected", "workable", "acme-analytics")
    assert "workable_shortcode" in d.evidence and d.fetches <= MAX_FETCHES + 1


async def test_workable_shortcode_unresolved_leaves_token_none(mock):
    page(mock, "https://acme.com", '<a href="https://apply.workable.com/j/ZZZ">Job</a>')
    page(mock, "https://apply.workable.com/j/ZZZ", "<html></html>")
    d = await run()
    assert (d.ats, d.token) == ("workable", None) and "workable" in d.notes


async def test_follow_open_roles_link_by_text(mock):
    page(mock, "https://acme.com", '<a href="/company/careers">Careers</a>')
    page(
        mock,
        "https://acme.com/company/careers",
        '<p>Join</p><a href="/open-roles">Explore all roles</a>',
    )
    page(
        mock,
        "https://acme.com/open-roles",
        '<iframe src="https://boards.greenhouse.io/embed/job_board?for=acmeco"></iframe>',
    )
    d = await run()
    assert (d.ats, d.token) == ("greenhouse", "acmeco")


async def test_follow_careers_subdomain_from_link(mock):
    page(mock, "https://acme.com", '<a href="https://careers.acme.com/">Careers</a>')
    page(mock, "https://careers.acme.com", '<a href="https://jobs.lever.co/acme">All jobs</a>')
    d = await run()
    assert (d.ats, d.token) == ("lever", "acme")


async def test_fallback_open_roles_when_nothing_linked(mock):
    page(mock, "https://acme.com", "<p>hello</p>")
    for u in ("/careers", "/jobs", "/careers/jobs"):
        mock.get(url__regex=r"https://acme\.com" + u + r"/?$").respond(404)
    mock.get(url__regex=r"https://(careers|jobs)\.acme\.com/?$").respond(404)
    page(
        mock,
        "https://acme.com/open-roles",
        '<a href="https://jobs.ashbyhq.com/acme">Roles</a>',
    )
    d = await run()
    assert (d.ats, d.token) == ("ashby", "acme")
    assert d.fetches <= MAX_FETCHES


async def test_fetch_cap_holds_on_dead_ends(mock):
    page(
        mock,
        "https://acme.com",
        "".join(f'<a href="/careers/p{i}">jobs {i}</a>' for i in range(20)),
    )
    mock.get(url__regex=r"https://acme\.com/.*").respond(200, text="<p>x</p>", headers=HTML)
    mock.get(url__regex=r"https://(careers|jobs)\.acme\.com/?.*").respond(404)
    d = await run()
    assert d.fetches <= MAX_FETCHES + 1
