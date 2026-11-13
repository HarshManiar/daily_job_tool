from sqlalchemy import text

from daily_job_tool.match.profile import load_profile
from daily_job_tool.normalize.models import NORMALIZER_VERSION
from daily_job_tool.pipeline.renormalize import renormalize, rescore
from tests.pipeline.helpers import CTX, FakeAdapter, crawl, job, rows, scalar, seed

PROFILE = load_profile("tests/fixtures/profile.pharma.yaml")


async def test_visa_exclusion_stored_not_dropped(db_clean):
    eng = db_clean
    sid = await seed(eng)
    ad = FakeAdapter().push(
        [
            job("1", "Senior Data Analyst", "Edison, NJ", desc="<p>No visa sponsorship.</p>"),
            job(
                "2",
                "Data Engineer",
                "Austin, TX",
                desc="<p>SQL Python</p><p>3+ years of experience</p>",
            ),
        ]
    )
    await crawl(eng, sid, ad, 0, profile=PROFILE)
    r = (await rows(eng, "SELECT * FROM jobs WHERE title = 'Senior Data Analyst'"))[0]
    assert r.match_status == "excluded" and "visa:not_available" in r.match_reasons
    assert r.visa_status == "not_available" and "sponsorship" in r.visa_evidence.lower()
    assert r.location_rank == 0 and r.match_tier == 2
    r2 = (await rows(eng, "SELECT * FROM jobs WHERE title = 'Data Engineer'"))[0]
    assert r2.years_min == 3 and r2.location_rank == 3 and r2.fit_score == 2
    assert r2.match_status != "excluded"


async def test_location_ranks_at_ingest(db_clean):
    eng = db_clean
    sid = await seed(eng)
    locs = {
        "a": ("Edison, NJ", 0), "b": ("Jersey City, NJ", 0), "c": ("Brooklyn, NY", 1),
        "d": ("Remote - US", 2), "e": ("Austin, TX", 3),
    }  # fmt: skip
    ad = FakeAdapter().push([job(k, "Data Analyst", v[0]) for k, v in locs.items()])
    await crawl(eng, sid, ad, 0, profile=PROFILE)
    for k, (_, rank) in locs.items():
        assert (
            await scalar(
                eng,
                "SELECT location_rank FROM jobs WHERE apply_url LIKE :u",
                u=f"%/jobs/{k}",
            )
            == rank
        ), k


async def test_renormalize_populates_match_columns_on_old_rows(db_clean):
    eng = db_clean
    sid = await seed(eng)
    ad = FakeAdapter().push(
        [job("1", "Data Analyst", "Edison, NJ", desc="<p>No sponsorship. SQL</p>")]
    )
    await crawl(eng, sid, ad, 0, profile=PROFILE)
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "UPDATE jobs SET normalizer_version = 1, match_status = 'include', match_tier = NULL, "
                "match_reasons = '{}', visa_status = 'not_stated', location_rank = 3, fit_score = 0"
            )
        )
    assert await renormalize(eng, CTX, profile=PROFILE) == 1
    r = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert (
        r.normalizer_version == NORMALIZER_VERSION
        and r.match_status == "excluded"
        and r.location_rank == 0
    )
    assert r.match_tier == 2

    async with eng.begin() as conn:
        await conn.execute(text("UPDATE jobs SET match_status = 'include', match_reasons = '{}'"))
    assert await rescore(eng, PROFILE) == 1
    assert (await rows(eng, "SELECT match_status FROM jobs"))[0].match_status == "excluded"


async def test_rescore_recomputes_text_signals_and_company(db_clean):
    eng = db_clean
    sid = await seed(eng)
    desc = (
        "<p>We do not currently sponsor, but H-1B transfers are accepted.</p>"
        "<p>Requirements</p><p>3+ years of experience with SQL</p>"
    )
    ad = FakeAdapter().push([job("1", "Data Analyst", "Edison, NJ", desc=desc)])
    await crawl(eng, sid, ad, 0, profile=PROFILE)
    async with eng.begin() as conn:  # simulate rows stored by an older rule set
        await conn.execute(
            text(
                "UPDATE jobs SET visa_status = 'not_available', years_min = 9, "
                "employment_type = 'contract', match_status = 'excluded', norm_flags = '{}'"
            )
        )
    assert await rescore(eng, PROFILE) == 1
    r = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert r.visa_status == "offered" and r.years_min == 3
    assert r.employment_type != "contract" and r.match_status != "excluded"
    async with eng.begin() as conn:
        await conn.execute(text("UPDATE companies SET exclude_reason = 'x'"))
    assert await rescore(eng, PROFILE, company_id=999999) == 0
    assert (await rows(eng, "SELECT match_status FROM jobs"))[0].match_status != "excluded"
    cid = await scalar(eng, "SELECT id FROM companies")
    assert await rescore(eng, PROFILE, company_id=cid) == 1
    assert (await rows(eng, "SELECT match_status FROM jobs"))[0].match_status == "excluded"


async def test_range_checks_reject(db_clean):
    import pytest
    from sqlalchemy.exc import IntegrityError

    eng = db_clean
    sid = await seed(eng)
    await crawl(eng, sid, FakeAdapter().push([job("1", "Data Analyst", "Edison, NJ")]), 0)
    for col, val in [
        ("years_min", -1), ("years_max", -2), ("location_rank", 4), ("industry_rank", 2),
        ("match_tier", 0), ("match_tier", 4),
    ]:  # fmt: skip
        with pytest.raises(IntegrityError):
            async with eng.begin() as conn:
                await conn.execute(text(f"UPDATE jobs SET {col} = {val}"))  # noqa: S608
