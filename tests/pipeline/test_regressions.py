"""Regression tests for the task 1.7 review findings."""

import asyncio

import pytest
from sqlalchemy import text

from daily_job_tool.http.errors import FetchError
from daily_job_tool.pipeline import ingest as ing
from daily_job_tool.pipeline import renormalize as rn
from tests.pipeline.helpers import CTX, FakeAdapter, FakeT3, at, crawl, job, rows, scalar, seed

V = ing.NORMALIZER_VERSION


async def posting(engine, sid, ext):
    return (
        await rows(
            engine, "SELECT * FROM postings WHERE source_id=:s AND external_id=:e", s=sid, e=ext
        )
    )[0]


def bump_version(monkeypatch, v=V + 1):
    monkeypatch.setattr(ing, "NORMALIZER_VERSION", v)


# 1. non-US decision is re-evaluated ----------------------------------------------------------
async def test_non_us_skip_reevaluated_after_version_bump(db_clean, monkeypatch):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    j = job("a", loc="London, UK", needs_detail=True)
    fa.push([j]).push([j]).push([j]).push([j])
    r = await crawl(eng, sid, fa, 0)
    assert r.skipped_non_us == 1
    p = await posting(eng, sid, "a")
    assert p.job_id is None and p.payload == {"_skipped_v": V} and len(p.payload_hash) == 32
    r = await crawl(eng, sid, fa, 1)  # same version: nothing to do
    assert r.skipped_non_us == 0 and await scalar(eng, "SELECT count(*) FROM jobs") == 0

    bump_version(monkeypatch)
    n_detail = len(fa.detail_calls)
    r = await crawl(eng, sid, fa, 2)  # still non-US: marker refreshed, no detail call
    assert r.skipped_non_us == 1 and len(fa.detail_calls) == n_detail
    assert (await posting(eng, sid, "a")).payload == {"_skipped_v": V + 1}

    bump_version(monkeypatch, V + 2)
    monkeypatch.setattr(ing, "is_us_eligible", lambda n: (True, False))
    r = await crawl(eng, sid, fa, 3)  # now eligible: gets a job (and its detail)
    assert r.jobs_created == 1 and len(fa.detail_calls) == n_detail + 1
    p = await posting(eng, sid, "a")
    assert p.job_id is not None and p.payload["loc"] == "London, UK"


async def test_legacy_empty_payload_is_refreshed(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    j = job("a", loc="London, UK")
    fa.push([j]).push([j])
    await crawl(eng, sid, fa, 0)
    async with eng.begin() as conn:
        await conn.execute(text("UPDATE postings SET payload = '{}'::jsonb"))
    r = await crawl(eng, sid, fa, 1)
    assert r.skipped_non_us == 1
    assert (await posting(eng, sid, "a")).payload == {"_skipped_v": V}


async def test_san_francisco_or_remote_canada_is_us(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("a", loc="San Francisco or Remote (Canada)")])
    r = await crawl(eng, sid, fa, 0)
    assert r.skipped_non_us == 0 and r.jobs_created == 1


# 2. a failing detail call must not block closing ---------------------------------------------
class BadDetail(FakeAdapter):
    bad: frozenset[str] = frozenset({"bad"})

    async def fetch_detail(self, src, j, http):
        if j.external_id in self.bad:
            raise ValueError("404 detail")
        return await super().fetch_detail(src, j, http)


async def test_failing_detail_does_not_block_closing(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = BadDetail()
    keep, gone = job("k", "Keep Engineer"), job("g", "Gone Engineer")
    bad = job("bad", "Bad Engineer", needs_detail=True)
    fa.push([keep, gone])
    for _ in range(3):
        fa.push([keep, bad])
    await crawl(eng, sid, fa, 0)
    r1 = await crawl(eng, sid, fa, 1)
    assert r1.outcome == "ok" and r1.complete and r1.detail_failures == 1
    assert "bad" in (await scalar(eng, "SELECT error FROM crawl_runs WHERE id=:i", i=r1.run_id))
    assert (await posting(eng, sid, "g")).missed_crawls == 1
    r2 = await crawl(eng, sid, fa, 2)
    assert r2.outcome == "ok" and r2.postings_closed == 1
    assert (await posting(eng, sid, "g")).closed_at == at(2)
    assert await scalar(eng, "SELECT last_outcome FROM sources") == "ok"


async def test_many_detail_failures_make_crawl_partial(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = BadDetail()
    fa.bad = frozenset({f"b{i}" for i in range(5)})
    g = job("g", "Gone Engineer")
    fa.push([g]).push([job(f"b{i}", f"Bad {i}", needs_detail=True) for i in range(5)])
    await crawl(eng, sid, fa, 0)
    r = await crawl(eng, sid, fa, 1)
    assert r.outcome == "partial" and not r.complete and r.detail_failures == 5
    assert (await posting(eng, sid, "g")).missed_crawls == 0


# 3. stale job fields after the primary posting changes ---------------------------------------
async def test_job_fields_follow_new_primary(db_clean):
    eng = db_clean
    s1 = await seed(eng, adapter="fake_t1", token="a")
    s3 = await seed(eng, adapter="fake_t3", token="b")
    a, b = FakeAdapter(), FakeT3()
    a.push([job("x1", apply_url="https://t1.example/apply", desc="<p>tier one</p>")])
    a.push([]).push([])
    t3 = job("y1", apply_url="https://t3.example/apply", desc="<p>tier three</p>")
    b.push([t3]).push([t3]).push([t3])
    await crawl(eng, s1, a, 0)
    await crawl(eng, s3, b, 0)
    j = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert j.apply_url == "https://t1.example/apply" and "tier one" in j.description_html
    for d in (1, 2):
        await crawl(eng, s1, a, d)
        await crawl(eng, s3, b, d)
    j = (await rows(eng, "SELECT * FROM jobs"))[0]
    y1 = await posting(eng, s3, "y1")
    assert j.closed_at is None and j.primary_posting_id == y1.id
    assert j.apply_url == "https://t3.example/apply"
    assert "tier three" in j.description_html


# 4. no job-id churn on re-key ----------------------------------------------------------------
async def test_rekey_keeps_job_id(db_clean, monkeypatch):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    j = job("a")
    fa.push([j]).push([j])
    await crawl(eng, sid, fa, 0)
    before = (await rows(eng, "SELECT * FROM jobs"))[0]
    bump_version(monkeypatch)
    orig = ing.base_dedupe_key
    monkeypatch.setattr(ing, "base_dedupe_key", lambda n: "v2" + orig(n))
    r = await crawl(eng, sid, fa, 1)
    after = await rows(eng, "SELECT * FROM jobs")
    assert len(after) == 1 and r.jobs_created == 0 and r.jobs_closed == 0
    assert after[0].id == before.id and after[0].first_seen_at == before.first_seen_at
    assert after[0].dedupe_key.startswith("v2") and after[0].closed_at is None
    assert after[0].normalizer_version == V + 1


# 5. mass-disappearance guard fires once per episode ------------------------------------------
async def _guard_run(eng, script):
    sid = await seed(eng)
    fa = FakeAdapter()
    full = [job(f"J{i}", f"Role {i}") for i in range(10)]
    fa.push(full)
    for step in script:
        fa.push(full if step == "full" else [])
    await crawl(eng, sid, fa, 0)
    out = []
    for d in range(1, len(script) + 1):
        r = await crawl(eng, sid, fa, d)
        out.append((r.outcome, r.postings_closed))
    return out


async def test_guard_empty_then_empty_closes_on_second_miss(db_clean):
    out = await _guard_run(db_clean, ["empty"] * 3)
    assert out == [("suspicious", 0), ("ok", 0), ("ok", 10)]


async def test_guard_recovery_after_suspicious(db_clean):
    out = await _guard_run(db_clean, ["empty", "full"])
    assert out == [("suspicious", 0), ("ok", 0)]
    assert await scalar(db_clean, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 10
    assert await scalar(db_clean, "SELECT max(missed_crawls) FROM postings") == 0


async def test_guard_recovery_after_first_miss(db_clean):
    out = await _guard_run(db_clean, ["empty", "empty", "full", "empty"])
    # suspicious, miss=1, board returns (reset), then a fresh episode is suspicious again
    assert out == [("suspicious", 0), ("ok", 0), ("ok", 0), ("suspicious", 0)]
    assert await scalar(db_clean, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 10


async def test_guard_does_not_retrip_after_misses_recorded(db_clean):
    out = await _guard_run(db_clean, ["empty"] * 4)
    assert out == [("suspicious", 0), ("ok", 0), ("ok", 10), ("ok", 0)]


# 6. write-phase failure ----------------------------------------------------------------------
async def test_write_phase_failure_rolls_back_and_records(db_clean, monkeypatch):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("a"), job("b", "Beta Engineer")])

    async def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ing, "refresh_jobs", boom)
    r = await crawl(eng, sid, fa, 0)
    assert r.outcome == "parse_error" and "boom" in (r.error or "")
    assert r.postings_new == 0 and r.jobs_created == 0
    assert await scalar(eng, "SELECT count(*) FROM postings") == 0
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 0
    run = (await rows(eng, "SELECT * FROM crawl_runs"))[0]
    assert run.outcome == "parse_error" and run.finished_at is not None and "boom" in run.error
    src = (await rows(eng, "SELECT * FROM sources"))[0]
    assert src.consecutive_failures == 1 and src.last_outcome == "parse_error"
    assert src.next_crawl_at is not None and src.next_crawl_at > at(0)


# 7. concurrent crawls of two sources of one company ------------------------------------------
async def test_concurrent_sources_consistent(db_clean):
    eng = db_clean
    s1 = await seed(eng, adapter="fake_t1", token="a")
    s3 = await seed(eng, adapter="fake_t3", token="b")
    a, b = FakeAdapter(), FakeT3()
    a.push([job("x1")]).push([]).push([])
    b.push([job("y1")]).push([]).push([])
    await asyncio.gather(crawl(eng, s1, a, 0), crawl(eng, s3, b, 0))
    js = await rows(eng, "SELECT * FROM jobs")
    assert len(js) == 1 and js[0].closed_at is None
    x1 = await posting(eng, s1, "x1")
    assert x1.job_id == js[0].id == (await posting(eng, s3, "y1")).job_id
    assert js[0].primary_posting_id == x1.id
    for d in (1, 2):
        await asyncio.gather(crawl(eng, s1, a, d), crawl(eng, s3, b, d))
    j = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert await scalar(eng, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 0
    assert j.closed_at is not None


# 8. version bump does not call fetch_detail again --------------------------------------------
async def test_version_bump_reuses_stored_payload(db_clean, monkeypatch):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    js = [
        job("a", "Alpha Engineer", needs_detail=True),
        job("b", "Beta Engineer", needs_detail=True),
    ]
    fa.push(js).push(js)
    await crawl(eng, sid, fa, 0)
    assert len(fa.detail_calls) == 2
    bump_version(monkeypatch)
    r = await crawl(eng, sid, fa, 1)
    assert r.detail_calls == 0 and len(fa.detail_calls) == 2
    assert await scalar(eng, "SELECT min(normalizer_version) FROM jobs") == V + 1
    assert (await posting(eng, sid, "a")).payload["detail"] is True


# 9. EWMA uses time since the last good crawl -------------------------------------------------
async def test_ewma_uses_last_good_crawl(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("a")]).push(FetchError("u", 500, "HTTP 500"))
    fa.push([job("a"), job("b", "B Engineer"), job("c", "C Engineer"), job("d", "D Engineer")])
    await crawl(eng, sid, fa, 0)
    await crawl(eng, sid, fa, 1)
    await crawl(eng, sid, fa, 2)
    # 3 new postings over the 48h since the last good crawl: 0.3 * (3 / 48 * 24)
    assert await scalar(eng, "SELECT new_postings_ewma FROM sources") == pytest.approx(0.45)


# 11. renormalize turning a job non-US --------------------------------------------------------
async def test_renormalize_non_us_detaches_and_closes(db_clean, monkeypatch):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("a")])
    await crawl(eng, sid, fa, 0)
    async with eng.begin() as conn:
        await conn.execute(text("UPDATE jobs SET normalizer_version = 0"))
    monkeypatch.setattr(rn, "is_us_eligible", lambda n: (False, False))
    await rn.renormalize(eng, CTX)
    p = await posting(eng, sid, "a")
    assert p.job_id is None and p.payload == {"_skipped_v": V} and len(p.payload_hash) == 32
    assert (await rows(eng, "SELECT closed_at FROM jobs"))[0].closed_at is not None


async def test_renormalize_rekeys_in_place(db_clean, monkeypatch):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("a")])
    await crawl(eng, sid, fa, 0)
    before = (await rows(eng, "SELECT * FROM jobs"))[0]
    async with eng.begin() as conn:
        await conn.execute(text("UPDATE jobs SET normalizer_version = 0"))
    orig = rn.base_dedupe_key
    monkeypatch.setattr(rn, "base_dedupe_key", lambda n: "v2" + orig(n))
    assert await rn.renormalize(eng, CTX) == 1
    after = await rows(eng, "SELECT * FROM jobs")
    assert len(after) == 1 and after[0].id == before.id
    assert after[0].dedupe_key.startswith("v2") and after[0].first_seen_at == before.first_seen_at


# nits ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(("day", "reset"), [(10, False), (20, True)])
async def test_posting_reopen_14_day_rule(db_clean, day, reset):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("a")]).push([]).push([]).push([job("a")])
    await crawl(eng, sid, fa, 0)
    jid = await scalar(eng, "SELECT id FROM jobs")
    await crawl(eng, sid, fa, 1)
    await crawl(eng, sid, fa, 2)
    assert await scalar(eng, "SELECT closed_at FROM jobs") is not None
    r = await crawl(eng, sid, fa, day)
    p = await posting(eng, sid, "a")
    assert p.closed_at is None and p.job_id == jid
    assert p.first_seen_at == (at(day) if reset else at(0))
    assert r.postings_new == (1 if reset else 0)
    j = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert j.id == jid and j.closed_at is None and j.first_seen_at == at(0)
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 1


async def test_revival_then_reopen_does_not_merge_same_source_reqs(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    d1, d2, d3 = job("d1"), job("d2"), job("d3")
    fa.push([d1, d2]).push([]).push([]).push([d3]).push([d3, d1])
    for d in range(5):
        await crawl(eng, sid, fa, d)
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 2
    j3 = (await posting(eng, sid, "d3")).job_id
    j1 = (await posting(eng, sid, "d1")).job_id
    assert j1 != j3
    assert (
        await scalar(
            eng, "SELECT count(*) FROM postings WHERE job_id=:j AND closed_at IS NULL", j=j3
        )
        == 1
    )
