import pytest
from sqlalchemy import text

from daily_job_tool.registry.importer import import_seeds
from daily_job_tool.registry.removal import CompanyNotFound, remove_company

HEADER = (
    "name,domain,careers_url,adapter,token,hq_country,size_bucket,tags,industry,"
    "is_staffing,exclude_reason,config\n"
)


async def test_remove_closes_everything_blocks_domain_and_blocks_reseed(db_clean, tmp_path):
    eng = db_clean
    p = tmp_path / "s.csv"
    p.write_text(HEADER + "Rm Co,rm.com,,fake_t1,rmco,US,,,pharma,,,\n")
    assert (await import_seeds(eng, p)).companies_new == 1
    r = await remove_company(eng, "rm.com", "asked", "a@rm.com")
    assert r.sources_removed == 1 and not r.already_removed
    again = await remove_company(eng, "Rm Co", "asked again")
    assert again.already_removed and again.sources_removed == 0
    async with eng.connect() as c:
        row = (
            await c.execute(text("SELECT status::text s, exclude_reason e FROM companies"))
        ).one()
        assert (row.s, row.e) == ("removed", "removal request: asked")
        assert (await c.execute(text("SELECT status::text FROM sources"))).scalar() == "removed"
        assert (
            await c.execute(text("SELECT reason FROM blocked_domains"))
        ).scalar() == "removal_request"
        assert (await c.execute(text("SELECT count(*) FROM removal_requests"))).scalar() == 1
    rep = await import_seeds(eng, p)
    assert rep.companies_new == 0 and len(rep.rejected) == 1
    async with eng.connect() as c:
        assert (await c.execute(text("SELECT status::text FROM companies"))).scalar() == "removed"
        assert (await c.execute(text("SELECT status::text FROM sources"))).scalar() == "removed"
    with pytest.raises(CompanyNotFound):
        await remove_company(eng, "ghost.example", "x")
