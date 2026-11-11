import json
from datetime import timedelta

import httpx
import pytest

from firsthand.adapters.greenhouse import GreenhouseAdapter
from firsthand.http.errors import FetchError, NotFound
from firsthand.normalize import NORMALIZER_VERSION
from firsthand.pipeline.ingest import (
    compute_next_crawl_at,
    ingest_source,
    payload_hash,
)
from tests.adapters._helpers import FIXTURES, fixture_client
from tests.pipeline.helpers import CTX, FakeAdapter, FakeT3, at, crawl, job, rows, scalar, seed


async def posting(engine, sid, ext):
    r = await rows(
        engine, "SELECT * FROM postings WHERE source_id=:s AND external_id=:e", s=sid, e=ext
    )
    return r[0]


class WarpGreenhouse(GreenhouseAdapter):
    """Fixture has no per-job detail responses; keep the list payload and only count calls."""

    async def fetch_detail(self, src, job, http):
        return job.model_copy(update={"needs_detail": False})


# (a) -----------------------------------------------------------------------------------------
async def test_greenhouse_warp_end_to_end(db_clean):
    eng = db_clean
    sid = await seed(eng, adapter="greenhouse", token="warp")
    raw = json.loads((FIXTURES / "greenhouse/warp/000.json").read_text())["jobs"]
    async with fixture_client("greenhouse", "warp") as c:
        r1 = await ingest_source(eng, sid, WarpGreenhouse(), c, CTX, now=at(0))
    assert r1.outcome == "ok" and r1.complete, r1.warnings[:3]
    assert r1.jobs_seen == len(raw) == r1.postings_new
    assert await scalar(eng, "SELECT count(*) FROM postings") == len(raw)
    n_jobs = await scalar(eng, "SELECT count(*) FROM jobs")
    assert n_jobs == r1.jobs_created == len(raw) - r1.skipped_non_us > 0
    run = (await rows(eng, "SELECT * FROM crawl_runs"))[0]
    assert run.outcome == "ok" and run.complete and run.jobs_seen == len(raw)
    assert run.jobs_new == len(raw) and run.finished_at is not None
    src = (await rows(eng, "SELECT * FROM sources"))[0]
    assert src.last_outcome == "ok" and src.consecutive_failures == 0
    assert src.open_postings == len(raw) and src.last_success_at is not None
    j = (await rows(eng, "SELECT * FROM jobs LIMIT 1"))[0]
    assert j.normalizer_version == NORMALIZER_VERSION and j.primary_posting_id is not None
    # greenhouse detail calls happen only for new/changed
    assert r1.detail_calls == len(raw)

    async with fixture_client("greenhouse", "warp") as c:
        r2 = await ingest_source(eng, sid, WarpGreenhouse(), c, CTX, now=at(1))
    assert r2.outcome == "ok"
    assert r2.postings_new == 0 and r2.postings_updated == 0 and r2.detail_calls == 0
    assert await scalar(eng, "SELECT count(*) FROM postings") == len(raw)
    assert await scalar(eng, "SELECT count(*) FROM jobs") == n_jobs
    assert await scalar(eng, "SELECT count(*) FROM crawl_runs") == 2


# (b) -----------------------------------------------------------------------------------------
async def test_closing_sequence_and_reopen(db_clean):
    eng = db_clean
    sid = await seed(eng)
    A, B, C = job("A", "Alpha Engineer"), job("B", "Beta Engineer"), job("C", "Gamma Engineer")
    fa = FakeAdapter()
    fa.push([A, B, C]).push([A, B]).push([A, B]).push([A, B, C])
    await crawl(eng, sid, fa, 0)
    assert await scalar(eng, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 3
    first_seen = (
        await rows(
            eng,
            "SELECT j.first_seen_at FROM jobs j JOIN postings p ON p.job_id=j.id "
            "WHERE p.external_id='C'",
        )
    )[0].first_seen_at

    r2 = await crawl(eng, sid, fa, 1)
    p = await posting(eng, sid, "C")
    assert p.missed_crawls == 1 and p.closed_at is None and r2.postings_closed == 0

    r3 = await crawl(eng, sid, fa, 2)
    p = await posting(eng, sid, "C")
    assert p.missed_crawls == 2 and p.closed_at == at(2) and r3.postings_closed == 1
    assert r3.jobs_closed == 1
    assert (await rows(eng, "SELECT closed_at FROM jobs WHERE id=:j", j=p.job_id))[
        0
    ].closed_at == at(2)
    assert await scalar(eng, "SELECT open_postings FROM sources") == 2

    await crawl(eng, sid, fa, 3)
    p = await posting(eng, sid, "C")
    assert p.closed_at is None and p.missed_crawls == 0
    jr = (await rows(eng, "SELECT * FROM jobs WHERE id=:j", j=p.job_id))[0]
    assert jr.closed_at is None and jr.first_seen_at == first_seen
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 3


# (c) -----------------------------------------------------------------------------------------
async def test_partial_and_failed_crawls_never_close(db_clean):
    eng = db_clean
    sid = await seed(eng)
    A, B = job("A", "Alpha Engineer"), job("B", "Beta Engineer")
    fa = FakeAdapter()
    fa.push([A, B]).push([A], complete=False).push(FetchError("u", 500, "HTTP 500"))
    fa.push(NotFound("u")).push(ValueError("bad shape")).push(
        FetchError("u", None, "ReadTimeout: x")
    )
    await crawl(eng, sid, fa, 0)
    r = await crawl(eng, sid, fa, 1)
    assert r.outcome == "partial" and not r.complete
    assert await scalar(eng, "SELECT max(missed_crawls) FROM postings") == 0
    assert await scalar(eng, "SELECT consecutive_failures FROM sources") == 0

    for i, outcome in enumerate(["http_error", "not_found", "parse_error", "timeout"][:3], start=1):
        r = await crawl(eng, sid, fa, 1 + i)
        assert r.outcome == outcome
        assert await scalar(eng, "SELECT consecutive_failures FROM sources") == i
        assert await scalar(eng, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 2
        assert await scalar(eng, "SELECT max(missed_crawls) FROM postings") == 0
    r = await crawl(eng, sid, fa, 5)
    assert r.outcome == "timeout"
    last = (await rows(eng, "SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1"))[0]
    assert last.outcome == "timeout" and last.finished_at is not None and last.error
    assert (await rows(eng, "SELECT last_outcome FROM sources"))[0].last_outcome == "timeout"


# (d) -----------------------------------------------------------------------------------------
async def test_mass_disappearance_empty(db_clean):
    eng = db_clean
    sid = await seed(eng)
    jobs10 = [job(f"J{i:02d}", f"Role {i}") for i in range(10)]
    fa = FakeAdapter()
    fa.push(jobs10).push([]).push([])
    await crawl(eng, sid, fa, 0)
    r = await crawl(eng, sid, fa, 1)
    assert r.outcome == "suspicious"
    assert await scalar(eng, "SELECT max(missed_crawls) FROM postings") == 0
    assert await scalar(eng, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 10
    nxt = await scalar(eng, "SELECT next_crawl_at FROM sources")
    assert nxt == at(1) + timedelta(hours=2)
    r = await crawl(eng, sid, fa, 2)  # second consecutive: rule applies normally
    assert r.outcome == "ok"
    assert await scalar(eng, "SELECT min(missed_crawls) FROM postings") == 1
    assert await scalar(eng, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 10


async def test_mass_disappearance_ratio(db_clean):
    eng = db_clean
    sid = await seed(eng)
    jobs25 = [job(f"J{i:02d}", f"Role {i}") for i in range(25)]
    fa = FakeAdapter()
    fa.push(jobs25).push(jobs25[:9]).push(jobs25[:9])
    await crawl(eng, sid, fa, 0)
    r = await crawl(eng, sid, fa, 1)  # 16/25 lost = 64% > 60%
    assert r.outcome == "suspicious"
    assert await scalar(eng, "SELECT max(missed_crawls) FROM postings") == 0
    r = await crawl(eng, sid, fa, 2)
    assert r.outcome == "ok"
    assert await scalar(eng, "SELECT count(*) FROM postings WHERE missed_crawls = 1") == 16


async def test_small_loss_not_suspicious(db_clean):
    eng = db_clean
    sid = await seed(eng)
    jobs25 = [job(f"J{i:02d}", f"Role {i}") for i in range(25)]
    fa = FakeAdapter()
    fa.push(jobs25).push(jobs25[:15])
    await crawl(eng, sid, fa, 0)
    r = await crawl(eng, sid, fa, 1)  # 40% lost
    assert r.outcome == "ok"
    assert await scalar(eng, "SELECT count(*) FROM postings WHERE missed_crawls = 1") == 10


# (e) -----------------------------------------------------------------------------------------
async def test_same_source_duplicates_not_merged(db_clean):
    eng = db_clean
    sid = await seed(eng)
    dups = [job(x, "Software Engineer") for x in ("d3", "d1", "d2")]
    fa = FakeAdapter()
    fa.push(dups).push(dups).push(list(reversed(dups)))
    await crawl(eng, sid, fa, 0)
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 3
    keys = await rows(
        eng,
        "SELECT p.external_id, j.dedupe_key FROM postings p JOIN jobs j "
        "ON j.id=p.job_id ORDER BY p.external_id",
    )
    assert [k.dedupe_key.count("#") for k in keys] == [0, 1, 1]
    assert keys[1].dedupe_key.endswith("#2") and keys[2].dedupe_key.endswith("#3")
    before = {k.external_id: k.dedupe_key for k in keys}
    for day in (1, 2):
        r = await crawl(eng, sid, fa, day)
        assert r.postings_new == 0 and r.jobs_created == 0
    after = await rows(
        eng, "SELECT p.external_id, j.dedupe_key FROM postings p JOIN jobs j ON j.id=p.job_id"
    )
    assert {k.external_id: k.dedupe_key for k in after} == before
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 3


async def test_duplicate_key_stable_when_first_closes(db_clean):
    eng = db_clean
    sid = await seed(eng)
    d1, d2 = job("d1"), job("d2")
    fa = FakeAdapter()
    fa.push([d1, d2]).push([d2]).push([d2])
    for day in (0, 1, 2):
        await crawl(eng, sid, fa, day)
    # d2 keeps its '#2' job; d1's job is closed
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 2
    assert await scalar(eng, "SELECT count(*) FROM jobs WHERE closed_at IS NULL") == 1
    p2 = await posting(eng, sid, "d2")
    assert (await rows(eng, "SELECT dedupe_key FROM jobs WHERE id=:j", j=p2.job_id))[
        0
    ].dedupe_key.endswith("#2")


# (f) -----------------------------------------------------------------------------------------
async def test_cross_source_dedupe(db_clean):
    eng = db_clean
    s1 = await seed(eng, adapter="fake_t3", token="careers")  # worse tier, seen first
    s2 = await seed(eng, adapter="fake_t1", token="ats")
    f3, f1 = FakeT3(), FakeAdapter()
    f3.push([job("x1")]).push([]).push([]).push([job("x1")])
    f1.push([job("y1")]).push([]).push([])
    await crawl(eng, s1, f3, 0)
    await crawl(eng, s2, f1, 1)
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 1
    assert await scalar(eng, "SELECT count(*) FROM postings WHERE job_id IS NOT NULL") == 2
    y1 = await posting(eng, s2, "y1")
    jr = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert jr.primary_posting_id == y1.id  # tier 1 beats tier 3 although seen later
    assert jr.first_seen_at == at(0)

    # close source 1's posting (needs 2 misses; the board has 0 jobs but guard needs >=5 open)
    await crawl(eng, s1, f3, 2)
    await crawl(eng, s1, f3, 3)
    assert (await posting(eng, s1, "x1")).closed_at is not None
    jr = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert jr.closed_at is None and jr.primary_posting_id == y1.id
    # close the second as well
    await crawl(eng, s2, f1, 4)
    await crawl(eng, s2, f1, 5)
    assert (await rows(eng, "SELECT closed_at FROM jobs"))[0].closed_at is not None


async def test_primary_repicked_when_primary_closes(db_clean):
    eng = db_clean
    s1 = await seed(eng, adapter="fake_t3", token="careers")
    s2 = await seed(eng, adapter="fake_t1", token="ats")
    f3, f1 = FakeT3(), FakeAdapter()
    f3.push([job("x1")]).push([job("x1")])
    f1.push([job("y1")]).push([]).push([])
    await crawl(eng, s1, f3, 0)
    await crawl(eng, s2, f1, 1)
    await crawl(eng, s2, f1, 2)
    await crawl(eng, s2, f1, 3)
    x1 = await posting(eng, s1, "x1")
    jr = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert (await posting(eng, s2, "y1")).closed_at is not None
    assert jr.closed_at is None and jr.primary_posting_id == x1.id


# (g) -----------------------------------------------------------------------------------------
async def test_us_filter(db_clean):
    eng = db_clean
    sid = await seed(eng)
    batch = [
        job("lon", "London Role", "London, UK"),
        job("rus", "Remote US Role", "Remote - US"),
        job("nyc", "NYC Role", "New York, NY"),
        job("rem", "Worldwide Role", "Remote"),
        job("unk", "Unknown Role", None),
        job("rde", "Remote DE Role", "Remote - Germany"),
    ]
    fa = FakeAdapter()
    fa.push(batch).push(batch)
    r = await crawl(eng, sid, fa, 0)
    assert r.skipped_non_us == 2 and r.jobs_created == 4
    for ext in ("lon", "rde"):
        p = await posting(eng, sid, ext)
        assert p.job_id is None and len(p.payload_hash) == 32
        assert p.payload == {"_skipped_v": NORMALIZER_VERSION}
    for ext in ("rus", "nyc", "rem", "unk"):
        assert (await posting(eng, sid, ext)).job_id is not None
    unk = (
        await rows(
            eng,
            "SELECT j.locations FROM jobs j JOIN postings p ON p.job_id=j.id "
            "WHERE p.external_id='unk'",
        )
    )[0].locations
    assert unk == [{"raw": "", "unknown": True}]
    r = await crawl(eng, sid, fa, 1)  # unchanged: skipped postings stay skipped, nothing new
    assert r.postings_new == 0 and r.postings_updated == 0 and r.skipped_non_us == 0
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 4


async def test_us_only_false_keeps_all(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("lon", "London Role", "London, UK"), job("nyc")])
    r = await crawl(eng, sid, fa, 0, us_only=False)
    assert r.skipped_non_us == 0 and r.jobs_created == 2
    assert (await posting(eng, sid, "lon")).payload["loc"] == "London, UK"


async def test_changed_posting_becomes_non_us_detaches_job(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("a")]).push([job("a", loc="London, UK", extra={"v": 2})])
    await crawl(eng, sid, fa, 0)
    r = await crawl(eng, sid, fa, 1)
    assert r.postings_updated == 1 and r.skipped_non_us == 1
    p = await posting(eng, sid, "a")
    assert p.job_id is None and p.payload == {"_skipped_v": NORMALIZER_VERSION}
    assert await scalar(eng, "SELECT count(*) FROM jobs WHERE closed_at IS NULL") == 0


# (h) -----------------------------------------------------------------------------------------
async def test_needs_detail_only_for_new_or_changed(db_clean):
    eng = db_clean
    sid = await seed(eng)
    a, b = (
        job("a", "Alpha Engineer", needs_detail=True),
        job("b", "Beta Engineer", needs_detail=True),
    )
    b2 = job("b", "Beta Engineer", needs_detail=True, extra={"rev": 2})
    fa = FakeAdapter()
    fa.push([a, b]).push([a, b2])
    r1 = await crawl(eng, sid, fa, 0)
    assert sorted(fa.detail_calls) == ["a", "b"] and r1.detail_calls == 2
    pa = await posting(eng, sid, "a")
    assert pa.payload["detail"] is True and pa.detail_fetched_at == at(0)
    assert pa.payload_hash == payload_hash(a.payload)  # list payload hash drives change detection
    r2 = await crawl(eng, sid, fa, 1)
    assert fa.detail_calls == ["a", "b", "b"] and r2.postings_updated == 1 and r2.detail_calls == 1
    assert (await posting(eng, sid, "b")).payload["rev"] == 2


async def test_detail_failure_is_ok_with_warning_and_retried(db_clean):
    eng = db_clean
    sid = await seed(eng)

    class Flaky(FakeAdapter):
        async def fetch_detail(self, src, j, http):
            if j.external_id == "b" and not getattr(self, "failed_once", False):
                self.failed_once = True
                raise FetchError("u", 500, "HTTP 500")
            return await super().fetch_detail(src, j, http)

    a, b = (
        job("a", "Alpha Engineer", needs_detail=True),
        job("b", "Beta Engineer", needs_detail=True),
    )
    fa = Flaky()
    fa.push([a, b]).push([a, b])
    r1 = await crawl(eng, sid, fa, 0)
    assert r1.outcome == "ok" and r1.detail_failures == 1 and r1.postings_new == 1
    assert "b:" in (await scalar(eng, "SELECT error FROM crawl_runs WHERE id = :i", i=r1.run_id))
    assert await scalar(eng, "SELECT count(*) FROM postings") == 1
    r2 = await crawl(eng, sid, fa, 1)
    assert r2.outcome == "ok" and r2.postings_new == 1


# (i) -----------------------------------------------------------------------------------------
async def test_revival_by_key_with_new_posting(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("old")]).push([]).push([]).push([job("new")])
    await crawl(eng, sid, fa, 0)
    jid = await scalar(eng, "SELECT id FROM jobs")
    await crawl(eng, sid, fa, 1)
    await crawl(eng, sid, fa, 2)
    assert await scalar(eng, "SELECT closed_at FROM jobs") is not None
    await crawl(eng, sid, fa, 3)
    assert await scalar(eng, "SELECT count(*) FROM jobs") == 1
    jr = (await rows(eng, "SELECT * FROM jobs"))[0]
    assert jr.id == jid and jr.closed_at is None and jr.first_seen_at == at(0)
    assert (await posting(eng, sid, "new")).job_id == jid
    assert jr.primary_posting_id == (await posting(eng, sid, "new")).id


# (k) -----------------------------------------------------------------------------------------
async def test_http_requests_recorded(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.make_request = True
    fa.push([job("a")])
    r = await crawl(eng, sid, fa, 0)
    assert r.http_requests >= 1
    assert await scalar(eng, "SELECT http_requests FROM crawl_runs") == r.http_requests


async def test_failure_records_http_requests_and_status(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.make_request = True
    fa.push(NotFound("u"))

    async def fetch(src, http):
        await http.get_json("https://example.com/api")
        raise NotFound("https://example.com/api")

    fa.fetch_jobs = fetch
    r = await crawl(eng, sid, fa, 0)
    run = (await rows(eng, "SELECT * FROM crawl_runs"))[0]
    assert (
        r.outcome == run.outcome == "not_found"
        and run.http_requests >= 1
        and run.http_status == 404
    )


# misc ----------------------------------------------------------------------------------------
async def test_ewma_and_next_crawl(db_clean):
    eng = db_clean
    sid = await seed(eng)
    fa = FakeAdapter()
    fa.push([job("a")]).push([job("a"), job("b", "Beta Engineer"), job("c", "Gamma Engineer")])
    await crawl(eng, sid, fa, 0)
    assert await scalar(eng, "SELECT new_postings_ewma FROM sources") == 0  # first crawl skipped
    await crawl(eng, sid, fa, 1)
    assert await scalar(eng, "SELECT new_postings_ewma FROM sources") == pytest.approx(0.6)
    nxt = await scalar(eng, "SELECT next_crawl_at FROM sources")
    lo, hi = timedelta(hours=24 / 1.6 * 0.9), timedelta(hours=24 / 1.6 * 1.1)
    assert at(1) + lo <= nxt <= at(1) + hi


def test_compute_next_crawl_at_bounds():
    for ewma, base in ((0, 24), (1, 12), (100, 4)):
        for _ in range(20):
            d = compute_next_crawl_at({"new_postings_ewma": ewma}, at(0)) - at(0)
            assert timedelta(hours=base * 0.9) - timedelta(seconds=1) <= d
            assert d <= timedelta(hours=base * 1.1) + timedelta(seconds=1)


async def test_unknown_source_raises(db_clean):
    from tests.adapters._helpers import mock_client

    async with mock_client(lambda r: httpx.Response(200)) as c:
        with pytest.raises(LookupError):
            await ingest_source(db_clean, 999, FakeAdapter(), c, CTX)
