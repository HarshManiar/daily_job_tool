import time

from sqlalchemy import text

from daily_job_tool.web.queries import FeedFilters, build_feed


async def test_feed_perf_20k(client, db):
    c = await db.company("Perf")
    async with db.engine.begin() as conn:
        await conn.execute(
            text(
                """INSERT INTO jobs (company_id, dedupe_key, title, title_norm, first_seen_at,
                last_seen_at, freshness_at, apply_url, normalizer_version, match_tier,
                location_rank, industry_rank, fit_score, match_status)
                SELECT :c, 'k' || g, 'Data Analyst ' || g, 'data analyst ' || g, now(), now(),
                now() - make_interval(secs => (g % 1000000) * 1.0), 'https://x.example/' || g, 1,
                CASE WHEN g % 5 = 0 THEN NULL ELSE (g % 3) + 1 END, g % 4, g % 2, g % 50,
                CASE WHEN g % 10 = 0 THEN 'excluded' ELSE 'include' END
                FROM generate_series(1, 20000) g"""
            ),
            {"c": c},
        )
        await conn.execute(text("ANALYZE jobs"))
    await client.get("/")  # warm-up
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        r = await client.get("/")
        times.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    best, median = min(times), sorted(times)[2]
    print(f"\nGET / with 20k jobs: min {best:.0f} ms, median {median:.0f} ms")
    assert median < 300

    where, order, p = build_feed(FeedFilters())
    async with db.engine.connect() as conn:
        plan = "\n".join(
            row[0]
            for row in await conn.execute(
                text(f"EXPLAIN SELECT j.id FROM jobs j WHERE {where} ORDER BY {order} LIMIT 51"),
                p,
            )
        )
    print(plan)
    assert "jobs_my_feed" in plan
