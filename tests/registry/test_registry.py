import pytest
from sqlalchemy import text

from daily_job_tool.http.errors import NotFound
from daily_job_tool.registry.importer import import_seeds, normalize_domain, slugify
from daily_job_tool.registry.verify import verify_sources
from tests.adapters._helpers import client_for
from tests.pipeline.helpers import FakeAdapter, job, rows

HEADER = (
    "name,domain,careers_url,adapter,token,hq_country,size_bucket,tags,industry,"
    "is_staffing,exclude_reason,config\n"
)


def write(tmp_path, body: str):
    p = tmp_path / "seeds.csv"
    p.write_text(HEADER + body)
    return p


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("https://www.Example.com/careers?x=1", "example.com"),
        ("careers.acme.io", "acme.io"),
        ("http://jobs.bar.co.uk/x", "bar.co.uk"),
        ("WWW.Foo.com.au", "foo.com.au"),
        ("example.com:8080/a", "example.com"),
        ("localhost", None),
        ("", None),
    ],
)
def test_normalize_domain(raw, want):
    assert normalize_domain(raw) == want


def test_slugify():
    assert slugify("Johnson & Johnson, Inc.") == "johnson-and-johnson-inc"
    assert slugify("Zoë Health") == "zoe-health"


SEEDS = (
    "Acme Bio,https://www.acmebio.com/jobs,,fake_t1,acmebio,us,201-1000,biotech;oncology,pharma/biotech,,,\n"
    'Beta Data,betadata.com,,fake_t1,betadata,US,,,healthcare data,,,"{""region"": ""eu""}"\n'
    "ExampleCorp,examplecorp.com,,,,US,,,pharma consulting,,current employer,\n"
    "Temp Staff,tempstaff.com,,,,US,,,,true,,\n"
    "Linked,linkedin.com,,fake_t1,li,US,,,,,,\n"
    "Careers on blocked,ok.com,https://jobs.indeed.com/x,,,US,,,,,,\n"
    "Bad adapter,bad.com,,nope,tok,US,,,,,,\n"
)


async def test_import_idempotent_and_rejections(db_clean, tmp_path):
    p = write(tmp_path, SEEDS)
    r1 = await import_seeds(db_clean, p)
    assert (r1.companies_new, r1.sources_new) == (4, 2)
    assert len(r1.rejected) == 3
    reasons = " | ".join(x[2] for x in r1.rejected)
    assert (
        "blocked domain linkedin.com" in reasons
        and "indeed.com" in reasons
        and "unknown adapter" in reasons
    )
    before = await rows(db_clean, "SELECT id, updated_at FROM companies ORDER BY id")

    r2 = await import_seeds(db_clean, p)
    assert (r2.companies_new, r2.companies_updated, r2.companies_unchanged) == (0, 0, 4)
    assert (r2.sources_new, r2.sources_updated, r2.sources_unchanged) == (0, 0, 2)
    assert await rows(db_clean, "SELECT id, updated_at FROM companies ORDER BY id") == before

    ax = (await rows(db_clean, "SELECT * FROM companies WHERE slug='examplecorp'"))[0]
    assert ax.exclude_reason == "current employer" and ax.domain == "examplecorp.com"
    acme = (await rows(db_clean, "SELECT * FROM companies WHERE slug='acme-bio'"))[0]
    assert acme.domain == "acmebio.com" and acme.tags == ["biotech", "oncology"]
    assert acme.hq_country == "US" and acme.industry == "pharma/biotech"
    assert (await rows(db_clean, "SELECT is_staffing FROM companies WHERE slug='temp-staff'"))[
        0
    ].is_staffing
    cfg = (await rows(db_clean, "SELECT config FROM sources WHERE token='betadata'"))[0].config
    assert cfg == {"region": "eu"}

    # real change bumps updated_at / counts; config change updates source
    p2 = write(tmp_path, SEEDS.replace("biotech;oncology", "biotech").replace('""eu""', '""us""'))
    r3 = await import_seeds(db_clean, p2)
    assert (r3.companies_updated, r3.sources_updated) == (1, 1)
    after = (await rows(db_clean, "SELECT updated_at FROM companies WHERE slug='acme-bio'"))[0]
    assert after.updated_at > [b for b in before if b.id == acme.id][0].updated_at


async def test_import_rejects_domain_in_blocked_table(db_clean, tmp_path):
    async with db_clean.begin() as conn:
        await conn.execute(
            text("INSERT INTO blocked_domains (domain, reason) VALUES ('removed.com', 'manual')")
        )
    rep = await import_seeds(db_clean, write(tmp_path, "Gone,www.removed.com,,,,US,,,,,,\n"))
    assert rep.companies_new == 0 and "blocked domain" in rep.rejected[0][2]


async def test_verify_marks_not_found_broken(db_clean, tmp_path, monkeypatch):
    await import_seeds(
        db_clean,
        write(
            tmp_path, "Good,good.com,,fake_t1,good,US,,,,,,\nGone,gone.com,,fake_t1,gone,US,,,,,,\n"
        ),
    )
    ad = FakeAdapter()
    ad.push([job("1"), job("2")])
    ad.push(NotFound("https://x"))
    from daily_job_tool.registry import verify as vmod

    monkeypatch.setattr(vmod.registry, "get_adapter", lambda name: ad)
    async with client_for(None) as c:
        out = await verify_sources(db_clean, c, concurrency=1)
    assert {(o.token, o.result, o.jobs) for o in out} == {
        ("good", "ok", 2),
        ("gone", "not_found", 0),
    }
    st = {
        r.token: r
        for r in await rows(
            db_clean,
            "SELECT token, status::text AS st, detected_by, detection_confidence FROM sources",
        )
    }
    assert st["good"].st == "active" and "jobs=2" in st["good"].detected_by
    assert st["gone"].st == "broken" and "not_found" in st["gone"].detected_by
    assert await rows(db_clean, "SELECT 1 FROM jobs") == []
    # already-verified / broken sources are not re-verified
    async with client_for(None) as c:
        assert await verify_sources(db_clean, c) == []


async def test_company_edit_rescores_open_jobs(db_clean, tmp_path):
    from tests.pipeline.helpers import crawl

    head = "Acme Bio,acmebio.com,,fake_t1,acmebio,US,,,{ind},{stf},{exc},\n"
    await import_seeds(db_clean, write(tmp_path, head.format(ind="", stf="", exc="")))
    sid = (await rows(db_clean, "SELECT id FROM sources"))[0].id
    await crawl(db_clean, sid, FakeAdapter().push([job("1", "Data Analyst", "Edison, NJ")]), 0)
    assert (await rows(db_clean, "SELECT match_status FROM jobs"))[0].match_status != "excluded"
    rep = await import_seeds(
        db_clean, write(tmp_path, head.format(ind="", stf="", exc="competitor"))
    )
    assert rep.companies_updated == 1
    assert (await rows(db_clean, "SELECT match_status FROM jobs"))[0].match_status == "excluded"
    # an unrelated edit (tags) does not need a rescore, and an unchanged re-import is a no-op
    rep = await import_seeds(
        db_clean, write(tmp_path, head.format(ind="", stf="", exc="competitor"))
    )
    assert rep.rescored_companies == set()


async def test_local_overlay_overrides_same_domain(db_clean, tmp_path):
    base = write(tmp_path, "Acme,acme.com,,,,US,,,software,,,\nBeta,beta.com,,,,US,,,fintech,,,\n")
    local = tmp_path / "seeds.local.csv"
    local.write_text(
        HEADER + "Acme Renamed,www.acme.com,,,,US,,,biotech,,,\nGamma,gamma.com,,,,US,,,,,,\n"
    )
    rep = await import_seeds(db_clean, base, local_path=local)
    assert rep.companies_new == 3
    got = {
        r.domain: r
        for r in await rows(
            db_clean, "SELECT domain::text AS domain, name, industry FROM companies"
        )
    }
    assert got["acme.com"].name == "Acme Renamed" and got["acme.com"].industry == "biotech"
    assert set(got) == {"acme.com", "beta.com", "gamma.com"}


async def test_missing_local_file_is_fine(db_clean, tmp_path):
    base = write(tmp_path, "Acme,acme.com,,,,US,,,software,,,\n")
    rep = await import_seeds(db_clean, base, local_path=tmp_path / "seeds.local.csv")
    assert rep.companies_new == 1


async def test_profile_exclude_companies_marks_excluded(db_clean, tmp_path):
    base = write(tmp_path, "Acme Inc.,acme.com,,,,US,,,software,,,\nBeta,beta.com,,,,US,,,,,,\n")
    await import_seeds(db_clean, base, exclude_companies=["ACME"])
    got = {
        r.slug: r.exclude_reason
        for r in await rows(db_clean, "SELECT slug::text AS slug, exclude_reason FROM companies")
    }
    assert got["acme-inc"] and got["beta"] is None
    # dropping the profile entry clears the exclusion on the next import
    await import_seeds(db_clean, base)
    got = {
        r.slug: r.exclude_reason
        for r in await rows(db_clean, "SELECT slug::text AS slug, exclude_reason FROM companies")
    }
    assert got["acme-inc"] is None
