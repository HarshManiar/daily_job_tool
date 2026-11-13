"""Each Phase 3 filter individually and combined, plus URL-state and API parity."""

import pytest

from daily_job_tool.web.queries import FeedFilters
from tests.web.test_pages import titles


async def _titles(client, qs: str) -> set[str]:
    r = await client.get("/?" + qs)
    assert r.status_code == 200, r.text[:300]
    return set(titles(r.text))


@pytest.fixture
async def seeded(db):
    a = await db.company("Alpha Pharma")
    b = await db.company("Beta Systems")
    await db.job(a, title="A-remote-sen", remote_type="remote", seniority="senior",
                 function="data", tech_tags=["python", "sql"], employment_type="full_time",
                 salary_min_usd=130000, salary_max_usd=150000, years_min=5)  # fmt: skip
    await db.job(b, title="B-hybrid-mid", remote_type="hybrid", seniority="mid",
                 function="engineering", tech_tags=["go"], employment_type="contract",
                 years_min=8)  # fmt: skip
    await db.job(b, title="B-onsite-unk", remote_type="onsite", seniority="unknown",
                 function="data", tech_tags=["sql", "dbt"], employment_type="unknown",
                 match_status="flagged", salary_min_usd=90000)  # fmt: skip
    await db.job(
        a, title="A-intern", remote_type="unknown", seniority="intern", employment_type="internship"
    )
    return db


async def test_each_filter(client, seeded):
    t = lambda qs: _titles(client, qs)  # noqa: E731
    assert await t("company=beta") == {"B-hybrid-mid", "B-onsite-unk"}
    assert await t("company=ALPHA+pha") == {"A-remote-sen", "A-intern"}
    assert await t("company=%25") == set()  # LIKE wildcard is escaped
    assert await t("remote=remote") == {"A-remote-sen"}
    assert await t("remote=hybrid") == {"B-hybrid-mid"}
    assert await t("remote=onsite") == {"B-onsite-unk"}
    assert await t("hide_no_pay=1") == {"A-remote-sen", "B-onsite-unk"}
    assert await t("max_years=6") == {"A-remote-sen", "B-onsite-unk", "A-intern"}  # null ok
    assert await t("max_years=3") == {"B-onsite-unk", "A-intern"}
    assert await t("seniority=senior") == {"A-remote-sen"}
    assert await t("seniority=senior&seniority=mid") == {"A-remote-sen", "B-hybrid-mid"}
    assert await t("function=data") == {"A-remote-sen", "B-onsite-unk"}
    assert await t("tags=sql") == {"A-remote-sen", "B-onsite-unk"}
    assert await t("tags=go,dbt") == {"B-hybrid-mid", "B-onsite-unk"}
    assert await t("tags=go&tags=python") == {"B-hybrid-mid", "A-remote-sen"}
    assert await t("emp=full_time") == {"A-remote-sen"}
    assert await t("emp=full_time_unknown") == {"A-remote-sen", "B-onsite-unk"}
    assert await t("clean_only=1") == {"A-remote-sen", "B-hybrid-mid", "A-intern"}
    assert await t("") == {"A-remote-sen", "B-hybrid-mid", "B-onsite-unk", "A-intern"}


async def test_combined_and_min_pay_interplay(client, seeded):
    assert await _titles(client, "function=data&tags=sql&max_years=6&clean_only=1") == {
        "A-remote-sen"
    }
    assert await _titles(client, "company=beta&function=data&emp=full_time_unknown") == {
        "B-onsite-unk"
    }
    # hide_no_pay wins over pay_unknown=include
    assert await _titles(client, "min_pay=100000&hide_no_pay=1") == {"A-remote-sen"}
    assert await _titles(client, "min_pay=100000") >= {"A-remote-sen", "B-hybrid-mid"}


async def test_sort_newest(client, db):
    c = await db.company("Sorty")
    await db.job(c, title="old-best", match_tier=1, age_days=5)
    await db.job(c, title="new-meh", age_days=0.1)
    from tests.web.test_pages import titles as tt

    best = tt((await client.get("/")).text)
    newest = tt((await client.get("/?sort=newest")).text)
    assert best == ["old-best", "new-meh"] and newest == ["new-meh", "old-best"]


async def test_bad_values_rejected(client, seeded):
    for qs in ("remote=mars", "seniority=wizard", "emp=x", "sort=random", "tags=%3Bdrop",
               "function=A;B", "max_years=99999"):  # fmt: skip
        assert (await client.get("/?" + qs)).status_code == 422, qs


async def test_url_state_roundtrip(client, seeded):
    f = FeedFilters.from_qs("tags=sql,python&seniority=senior&posted24=1&sort=newest&min_pay=1")
    again = FeedFilters.from_qs(f.query_string())
    assert again == f and f.tags == ["sql", "python"]
    page = (await client.get("/?" + f.query_string())).text
    assert 'name="posted24" value="1" checked' in page
    assert 'value="senior" checked' in page
    assert 'value="python, sql"' not in page and 'value="sql, python"' in page


async def test_api_parity(client, seeded):
    for qs, want in [
        ("company=beta", {"B-hybrid-mid", "B-onsite-unk"}),
        ("remote=remote", {"A-remote-sen"}),
        ("seniority=senior&seniority=mid", {"A-remote-sen", "B-hybrid-mid"}),
        ("tags=go,dbt&sort=newest", {"B-hybrid-mid", "B-onsite-unk"}),
        ("max_years=6&hide_no_pay=1&clean_only=1", {"A-remote-sen"}),
        ("emp=full_time_unknown&function=data", {"A-remote-sen", "B-onsite-unk"}),
    ]:
        r = await client.get("/api/v1/jobs?" + qs)
        assert r.status_code == 200
        body = r.json()
        assert {i["title"] for i in body["items"]} == want, qs
        assert body["total"] == len(want)
        assert _titles_set(await client.get("/?" + qs)) == want
    assert (await client.get("/api/v1/jobs?remote=mars")).status_code == 422


def _titles_set(r):
    return set(titles(r.text))
