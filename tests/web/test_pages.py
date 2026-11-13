import re

from sqlalchemy import text


def titles(html: str) -> list[str]:
    return re.findall(r'class="job-title" href="/jobs/\d+">([^<]*)<', html)


async def test_ordering(client, db):
    c = await db.company("Ord")
    await db.job(c, title="J-fresh-none", age_days=0.1)
    await db.job(c, title="J-t2", match_tier=2, location_rank=0, age_days=5)
    await db.job(c, title="J-t1-loc3", match_tier=1, location_rank=3)
    await db.job(c, title="J-t1-loc1-ind1", match_tier=1, location_rank=1, industry_rank=1)
    await db.job(c, title="J-t1-loc1-ind0", match_tier=1, location_rank=1, industry_rank=0)
    await db.job(c, title="J-t1-loc1-ind0-fit9", match_tier=1, location_rank=1, industry_rank=0,
                 fit_score=9)  # fmt: skip
    await db.job(c, title="J-t1-loc1-ind0-fit9-old", match_tier=1, location_rank=1,
                 industry_rank=0, fit_score=9, age_days=3)  # fmt: skip
    r = await client.get("/")
    assert r.status_code == 200
    assert titles(r.text) == [
        "J-t1-loc1-ind0-fit9",
        "J-t1-loc1-ind0-fit9-old",
        "J-t1-loc1-ind0",
        "J-t1-loc1-ind1",
        "J-t1-loc3",
        "J-t2",
        "J-fresh-none",
    ]


async def test_excluded_and_filtered_view(client, db):
    c = await db.company("Ex")
    await db.job(c, title="Visible")
    await db.job(
        c,
        title="NoVisa",
        match_status="excluded",
        match_reasons=["visa:not_available"],
        visa_status="not_available",
        visa_evidence="We cannot sponsor visas now",
    )
    await db.job(c, title="SevenYrJob", match_status="excluded", match_reasons=["years:min_7+"])
    home = (await client.get("/")).text
    assert "Visible" in home and "NoVisa" not in home and "SevenYrJob" not in home
    f = (await client.get("/filtered")).text
    assert "NoVisa" in f and "Visible" not in f
    assert "No visa sponsorship" in f and "We cannot sponsor visas now" in f
    assert "Requires 7+ years" in f
    only = (await client.get("/filtered", params={"reason": "years:min_7+"})).text
    assert "SevenYrJob" in only and "NoVisa" not in only


async def test_days_filter_and_closed(client, db):
    c = await db.company()
    await db.job(c, title="New", age_days=1)
    await db.job(c, title="Mid", age_days=10)
    await db.job(c, title="Old", age_days=20)
    await db.job(c, title="Closed", closed=True)
    assert set(titles((await client.get("/")).text)) == {"New", "Mid"}
    assert set(titles((await client.get("/?days=7")).text)) == {"New"}
    assert set(titles((await client.get("/?days=30")).text)) == {"New", "Mid", "Old"}
    assert set(titles((await client.get("/?days=all")).text)) == {"New", "Mid", "Old"}


async def test_filters(client, db):
    c = await db.company("F")
    await db.job(c, title="t1nj", match_tier=1, location_rank=0, industry_rank=0)
    await db.job(c, title="t2nyc", match_tier=2, location_rank=1)
    await db.job(c, title="t3rem", match_tier=3, location_rank=2, visa_status="offered")
    await db.job(c, title="notarget", location_rank=3)
    await db.job(c, title="rich", match_tier=1, salary_min_usd=150000, salary_max_usd=180000)
    await db.job(c, title="poor", match_tier=1, salary_min_usd=50000, salary_max_usd=60000)

    async def t(qs: str) -> set[str]:
        r = await client.get("/?" + qs)
        assert r.status_code == 200
        return set(titles(r.text))

    everything = await t("")
    assert await t("tier=1") == everything  # the tier filter is gone from the UI: ignored
    assert await t("tier=targets") == everything
    assert await t("place=nj") == {"t1nj"}
    assert await t("place=nyc") == {"t2nyc"}
    assert await t("place=remote") == {"t3rem"}
    assert await t("place=other") == {"notarget", "rich", "poor"}
    assert await t("industry_only=1") == {"t1nj"}
    assert await t("visa_only=1") == {"t3rem"}
    assert await t("min_pay=100000") == {"rich", "t1nj", "t2nyc", "t3rem", "notarget"}
    assert await t("min_pay=100000&pay_unknown=exclude") == {"rich"}
    assert await t("min_pay=&tier=any") >= {"poor"}
    assert (await client.get("/?tier=bogus")).status_code == 422


async def test_search(client, db):
    c = await db.company("S")
    await db.job(c, title="Senior Data Engineer", description_text="spark airflow")
    await db.job(c, title="Pharmacy Analyst", description_text="claims data")
    await db.job(c, title="Barista", description_text="coffee")
    assert titles((await client.get("/?q=airflow")).text) == ["Senior Data Engineer"]
    assert titles((await client.get("/?q=pharm")).text) == ["Pharmacy Analyst"]  # trigram/ILIKE
    assert titles((await client.get("/?q=%25_%27%3B--")).text) == []  # odd input is safe
    assert titles((await client.get("/?q=ana")).text) == ["Pharmacy Analyst"]


async def test_pagination_no_duplicates(client, db):
    c = await db.company("P")
    for i in range(120):
        await db.job(c, title=f"Job{i:03d}", fit_score=i % 7, age_days=0.01 * i)
    r1 = await client.get("/")
    assert "<strong>120</strong>" in r1.text and "showing 25" in r1.text
    seen = titles(r1.text)
    assert len(seen) == 25
    h = {"HX-Request": "true"}
    allt = list(seen)
    for off in (25, 50, 75):
        r = await client.get(f"/?offset={off}", headers=h)
        assert "<html" not in r.text and 'id="showing"' in r.text and "Load more" in r.text
        assert 'hx-trigger="intersect once, click"' in r.text
        allt += titles(r.text)
    r5 = await client.get("/?offset=100", headers=h)
    last = titles(r5.text)
    assert len(last) == 20 and "Load more" not in r5.text
    assert len(set(allt + last)) == 120
    # filter swap returns only the browse fragment
    r4 = await client.get("/?days=all", headers={**h, "HX-Target": "browse"})
    assert "<html" not in r4.text and "<strong>120</strong>" in r4.text and 'id="browse"' in r4.text


async def test_row_chips_and_apply(client, db):
    c = await db.company("Chips")
    await db.job(
        c,
        title="Chippy",
        match_tier=1,
        remote_type="remote",
        visa_status="offered",
        years_min=3,
        years_max=5,
        salary_min_usd=120000,
        salary_max_usd=150000,
        match_reasons=["employment:unknown", "date:undated"],
    )
    await db.job(c, title="Evil", apply_url="javascript:alert(1)")
    html = (await client.get("/")).text
    for s in ["$120K–$150K/yr", "Offers sponsorship", "3–5 yrs", "type not stated", "undated",
              "first seen", ">Remote<"]:  # fmt: skip
        assert s in html
    assert "T1" not in html and "Tier" not in html
    assert 'href="https://example.com/apply" target="_blank" rel="noopener noreferrer"' in html
    assert "javascript:" not in html


async def test_job_detail_sanitizes(client, db):
    c = await db.company("D")
    jid = await db.job(
        c,
        title="Detail",
        description_html='<p>Hi</p><script>alert(1)</script><img src=x onerror="alert(2)">'
        '<a href="javascript:alert(3)">x</a>',
        match_tier=2,
        visa_evidence="no sponsorship",
        visa_status="not_available",
    )
    async with db.engine.begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET norm_flags = ARRAY['salary:guess'] WHERE id = :i"), {"i": jid}
        )
    r = await client.get(f"/jobs/{jid}")
    assert r.status_code == 200
    assert "<p>Hi</p>" in r.text
    assert "<script>alert" not in r.text and "onerror" not in r.text
    assert "javascript:" not in r.text
    assert "no sponsorship" in r.text and "Says no sponsorship" in r.text
    assert (await client.get("/jobs/999999")).status_code == 404
    assert (await client.get("/jobs/abc")).status_code == 422


async def test_company_page(client, db):
    c = await db.company("Co Page", slug="co-page", industry="Biotech")
    await db.job(c, title="Open1")
    await db.job(c, title="Gone", match_status="excluded", match_reasons=["company:staffing"])
    await db.job(c, title="Shut", closed=True)
    r = await client.get("/companies/co-page")
    assert r.status_code == 200
    assert "Open1" in r.text and "Gone" in r.text and "Staffing agency" in r.text
    assert "Shut" not in r.text and "1 closed" in r.text and "3 opened" in r.text
    r404 = await client.get("/companies/nope")
    assert r404.status_code == 404 and "Not found" in r404.text


async def test_health_empty_and_populated(client, db):
    r = await client.get("/health")
    assert r.status_code == 200 and "never" in r.text
    c = await db.company("H")
    await db.job(c)
    async with db.engine.begin() as conn:
        sid = (
            await conn.execute(
                text(
                    "INSERT INTO sources (company_id, adapter, token, status, consecutive_failures,"
                    " last_outcome) VALUES (:c, 'greenhouse', 'tok', 'broken', 3, 'http_error') "
                    "RETURNING id"
                ),
                {"c": c},
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO crawl_runs (source_id, adapter, started_at, outcome, error) "
                "VALUES (:s, 'greenhouse', now(), 'http_error', 'boom 500')"
            ),
            {"s": sid},
        )
    r = await client.get("/health")
    assert r.status_code == 200
    for s in ["greenhouse/tok", "boom 500", "broken", "0 / 1", "include"]:
        assert s in r.text
    j = (await client.get("/api/v1/health")).json()
    assert j["status"] == "ok" and j["open_jobs"] == 1


async def test_bot_and_api(client, db):
    assert "DailyJobToolBot" in (await client.get("/bot")).text
    c = await db.company("A")
    for i in range(5):
        await db.job(c, title=f"x{i}")
    j = (await client.get("/api/v1/jobs?limit=3")).json()
    assert j["total"] == 5 and len(j["items"]) == 3 and j["has_more"] is True
    assert (await client.get("/api/v1/jobs?limit=201")).status_code == 422
    assert len((await client.get("/api/v1/jobs?limit=200&offset=3")).json()["items"]) == 2


async def test_security(client):
    r = await client.get("/health")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"
    for host in ["evil.example", "127.0.0.1.evil.example", "localhost.evil.com:8000"]:
        assert (await client.get("/", headers={"Host": host})).status_code == 400
    for host in ["localhost:8000", "127.0.0.1", "[::1]:8000"]:
        assert (await client.get("/bot", headers={"Host": host})).status_code == 200
    assert (await client.get("/static/app.css")).status_code == 200
    assert "content-security-policy" in (await client.get("/nope")).headers


def test_cli_refuses_public_host():
    from typer.testing import CliRunner

    from daily_job_tool.cli import app

    res = CliRunner().invoke(app, ["web", "--host", "0.0.0.0"])
    assert res.exit_code != 0
    assert "refusing" in res.output


async def test_bot_page_shows_contact_only_when_set(client, monkeypatch):
    from daily_job_tool import config

    monkeypatch.setenv("BOT_CONTACT", "")
    config.get_settings.cache_clear()
    html = (await client.get("/bot")).text
    assert "Contact:" not in html
    monkeypatch.setenv("BOT_CONTACT", "ops@example.org")
    config.get_settings.cache_clear()
    html = (await client.get("/bot")).text
    assert "Contact: ops@example.org" in html
    assert "DailyJobToolBot/0.1 (+ops@example.org)" in html
    config.get_settings.cache_clear()
