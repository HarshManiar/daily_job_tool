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
        await conn.execute(
            text(
                """UPDATE jobs SET
                remote_type = (ARRAY['remote','hybrid','onsite','unknown'])[1 + id % 4]::remote_type,
                seniority = (ARRAY['senior','mid','entry','unknown'])[1 + id % 4]::seniority,
                function = (ARRAY['data','engineering','sales','other'])[1 + id % 4],
                tech_tags = CASE WHEN id % 3 = 0 THEN ARRAY['sql','python'] ELSE '{}' END,
                years_min = CASE WHEN id % 2 = 0 THEN id % 10 END,
                salary_min_usd = CASE WHEN id % 3 = 0 THEN 100000 + id % 50000 END,
                employment_type = (ARRAY['full_time','unknown','contract'])[1 + id % 3]::emp_type"""
            )
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


COMBINED = (
    "tier=targets&remote=remote&max_years=6&tags=sql&seniority=senior&seniority=mid"
    "&hide_no_pay=1&emp=full_time_unknown&clean_only=1&posted48=1&function=data&company=perf"
)


async def test_combined_and_variant_queries_fast(client, db):
    await test_feed_perf_20k(client, db)  # seeds 20k rows (and re-checks the default)
    for qs in (COMBINED, "sort=newest", "tags=sql&sort=newest", "posted24=1", "q=analyst&days=30",
               "seniority=senior&function=data&remote=hybrid&days=all"):  # fmt: skip
        await client.get("/?" + qs)  # warm
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            r = await client.get("/?" + qs)
            times.append((time.perf_counter() - t0) * 1000)
            assert r.status_code == 200
        print(f"\nGET /?{qs[:50]}: median {sorted(times)[1]:.0f} ms")
        assert sorted(times)[1] < 300, qs
    t0 = time.perf_counter()
    assert (await client.get("/api/v1/jobs?" + COMBINED)).status_code == 200
    assert (time.perf_counter() - t0) * 1000 < 300
