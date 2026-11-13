from daily_job_tool.web import queries
from daily_job_tool.web.format import trend_chart


async def test_trend_sql_on_synthetic_jobs(db):
    c = await db.company("Trend")
    other = await db.company("Other")
    await db.job(c, title="a", age_days=1)  # opened this week-ish
    await db.job(c, title="b", age_days=1, first_seen_age_days=15, closed_age_days=2)
    await db.job(c, title="c", age_days=1, first_seen_age_days=40, closed_age_days=30)
    await db.job(c, title="ancient", age_days=1, first_seen_age_days=200)  # outside window
    await db.job(other, title="x", age_days=1)  # other company ignored
    async with db.engine.connect() as conn:
        t = await queries.company_trend(conn, c)
    assert len(t) == 13
    assert [r["week"] for r in t] == sorted(r["week"] for r in t)
    assert sum(r["opened"] for r in t) == 3  # a, b, c (ancient is outside 13 weeks)
    assert sum(r["closed"] for r in t) == 2
    assert t[-1]["week"].weekday() == 0  # ISO week starts


async def test_tier_counts(db):
    c = await db.company("Tiers")
    await db.job(c, title="t1", match_tier=1)
    await db.job(c, title="t1b", match_tier=1)
    await db.job(c, title="t2", match_tier=2)
    await db.job(c, title="none")
    await db.job(c, title="ex", match_tier=1, match_status="excluded")
    await db.job(c, title="closed", match_tier=3, closed=True)
    async with db.engine.connect() as conn:
        out = await queries.company_tier_counts(conn, c)
    assert out == {"1": 2, "2": 1, "3": 0, "other": 1, "open": 4}


async def test_company_page_renders_svg(client, db):
    c = await db.company("Komodo Test", slug="komodo-test")
    await db.job(c, title="a", match_tier=1)
    await db.job(c, title="b", first_seen_age_days=20, closed_age_days=3, age_days=1)
    r = await client.get("/companies/komodo-test")
    assert r.status_code == 200
    t = r.text
    assert 'role="img"' in t and "<title" in t and "<desc" in t
    assert (
        "<svg" in t and "<rect" in t and "tier 1: 1" in t and "<strong>1</strong> open roles" in t
    )
    assert "<script" not in t.split("<main>")[1]


def test_trend_chart_geometry():
    trend = [
        {"week": "2026-01-05", "opened": 4, "closed": 0},
        {"week": "2026-01-12", "opened": 0, "closed": 2},
    ]
    ch = trend_chart(trend)
    assert ch["max"] == 4 and len(ch["bars"]) == 4
    tallest = max(b["h"] for b in ch["bars"])
    assert tallest == max(b["h"] for b in ch["bars"] if b["kind"] == "opened")
    assert all(0 <= b["y"] and b["y"] + b["h"] <= ch["h"] for b in ch["bars"])
    assert trend_chart([])["bars"] == []
