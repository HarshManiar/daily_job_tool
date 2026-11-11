from sqlalchemy import text

from daily_job_tool.normalize import NORMALIZER_VERSION
from daily_job_tool.pipeline.ingest import ingest_source
from daily_job_tool.pipeline.renormalize import renormalize
from tests.adapters._helpers import fixture_client
from tests.pipeline.helpers import CTX, at, rows, scalar, seed
from tests.pipeline.test_ingest import WarpGreenhouse


async def test_renormalize_updates_outdated_jobs_without_http(db_clean):
    eng = db_clean
    sid = await seed(eng, adapter="greenhouse", token="warp")
    async with fixture_client("greenhouse", "warp") as c:
        await ingest_source(eng, sid, WarpGreenhouse(), c, CTX, now=at(0))
    n = await scalar(eng, "SELECT count(*) FROM jobs")
    assert await renormalize(eng, CTX) == 0  # nothing outdated

    async with eng.begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET normalizer_version = 0, seniority = 'exec', tech_tags = '{}'")
        )
        # one posting with an empty payload must be skipped
        await conn.execute(
            text(
                "UPDATE postings SET payload = '{}'::jsonb WHERE id = "
                "(SELECT primary_posting_id FROM jobs ORDER BY id LIMIT 1)"
            )
        )
    updated = await renormalize(eng, CTX)
    assert updated == n - 1
    assert (
        await scalar(
            eng, "SELECT count(*) FROM jobs WHERE normalizer_version = :v", v=NORMALIZER_VERSION
        )
        == n - 1
    )
    assert await scalar(eng, "SELECT count(*) FROM jobs WHERE seniority = 'exec'") == 1
    assert (
        len(
            await rows(
                eng, "SELECT 1 FROM jobs WHERE salary_min IS NOT NULL OR cardinality(tech_tags) > 0"
            )
        )
        > 0
    )
    assert await renormalize(eng, CTX, only_outdated=False) == n - 1
